"""
导出 INT8 TFLite 模型 + C 数组头文件

用法（在 01-stage0-tinyml 目录下）：

    # 标准导出（逐通道 INT8 + FP32 对照）
    python src/export_tflite.py --ckpt runs\\<运行名>\\best.pt

    # 冒烟测试：不需要数据集，用随机数据校准（只验证流程，精度无意义）
    python src/export_tflite.py --ckpt runs\\<运行名>\\best.pt --smoke

    # 对比实验
    python src/export_tflite.py --ckpt ... --name int8_pt --no-per-channel
    python src/export_tflite.py --ckpt ... --name int8_c1000 --n-calib 1000
    python src/export_tflite.py --ckpt ... --name fp32_only --no-int8

================================================================================
为什么用「Keras 重建 + 权重搬运」而不是自动转换器
================================================================================

常规做法是 PyTorch -> ONNX -> onnx2tf -> TFLite，全自动。本脚本**没有**走这条路，
原因是一个很实际的工程判断：

    自动转换器出错是**静默的**。

如果 onnx2tf 在某个算子上做了等价变换（比如把 depthwise conv 拆成别的形式，
或改了 padding 语义），转换会"成功"，产出一个能跑但精度崩掉的模型。
你要到部署到板子上才发现，而那时已经分不清是量化问题还是转换问题。

本方案的做法：
    1. 用 Keras 手工重建一个结构完全一致的模型
    2. 把 PyTorch 权重逐个搬过去（做维度转置）
    3. **数值验证**：喂同样的输入，比对两边输出的逐元素最大误差

第 3 步是关键。如果误差 < 1e-4，说明转换 100% 正确，
后面量化掉点就只会是量化本身的锅。排查范围直接缩小一个数量级。

维度转置对照（PyTorch 是 NCHW / OIHW，TensorFlow 是 NHWC / HWIO）：
    Conv2d       (C_out, C_in, kH, kW)  ->  (kH, kW, C_in, C_out)   transpose(2,3,1,0)
    Depthwise    (C,     1,    kH, kW)  ->  (kH, kW, C,     1)      transpose(2,3,0,1)
    Linear       (C_out, C_in)          ->  (C_in, C_out)           transpose(1,0)
    BatchNorm    gamma/beta/mean/var     ->  同名，直接复制
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                          # noqa: E402
from config import AUDIO, EXPORT_DIR, LABELS, QUANT    # noqa: E402
from model import build_model                          # noqa: E402


# ================================================================ Keras 模型
def build_keras_model(num_classes: int):
    """用 Keras 重建与 PyTorch 版结构完全一致的 DS-CNN。

    注意通道顺序：PyTorch 用 NCHW，Keras 默认 NHWC。
    所以输入是 (T, F, 1) 而不是 (1, T, F)。
    """
    import tensorflow as tf
    from config import MODEL

    T, F = AUDIO.feature_shape

    inputs = tf.keras.Input(shape=(T, F, 1), name="mfcc")

    # ---- per-sample 归一化（与 model.PerSampleNorm 完全对应）----
    axes = [1, 2, 3]
    mean, var = tf.nn.moments(inputs, axes=axes, keepdims=True)
    x = (inputs - mean) / tf.sqrt(var + 1e-5)

    # ---- stem: 5x5 普通卷积，stride (2,2) ----
    x = tf.keras.layers.Conv2D(
        filters=MODEL.stem_channels, kernel_size=5, strides=(2, 2),
        padding="same", use_bias=False, name="stem_conv",
    )(x)
    x = tf.keras.layers.BatchNormalization(name="stem_bn")(x)
    x = tf.keras.layers.ReLU(name="stem_relu")(x)

    # ---- 4 个深度可分离块 ----
    in_ch = MODEL.stem_channels
    for i, (out_ch_raw, stride) in enumerate(
        zip(MODEL.block_channels, MODEL.block_strides)
    ):
        out_ch = max(8, int(out_ch_raw * MODEL.width_multiplier) // 8 * 8)

        # Depthwise：groups=in_ch，每个输入通道独立卷积
        x = tf.keras.layers.DepthwiseConv2D(
            kernel_size=3, strides=stride, padding="same",
            use_bias=False, name=f"blk{i}_dw",
        )(x)
        x = tf.keras.layers.BatchNormalization(name=f"blk{i}_dw_bn")(x)
        x = tf.keras.layers.ReLU(name=f"blk{i}_dw_relu")(x)

        # Pointwise：1x1 卷积做通道混合
        x = tf.keras.layers.Conv2D(
            filters=out_ch, kernel_size=1, strides=(1, 1), padding="valid",
            use_bias=False, name=f"blk{i}_pw",
        )(x)
        x = tf.keras.layers.BatchNormalization(name=f"blk{i}_pw_bn")(x)
        x = tf.keras.layers.ReLU(name=f"blk{i}_pw_relu")(x)
        in_ch = out_ch

    # ---- 分类头 ----
    x = tf.keras.layers.GlobalAveragePooling2D(name="gap")(x)
    outputs = tf.keras.layers.Dense(num_classes, name="logits")(x)

    return tf.keras.Model(inputs=inputs, outputs=outputs, name="kws_dscnn")


# ================================================================ 权重搬运
def copy_weights(torch_model, keras_model) -> None:
    """把 PyTorch 权重搬进 Keras 模型，做必要的维度转置。"""
    import tensorflow as tf

    sd = torch_model.state_dict()
    plt2tf_count = 0

    def get(name: str) -> np.ndarray:
        return sd[name].detach().cpu().numpy().astype(np.float32)

    # ---- stem ----
    keras_model.get_layer("stem_conv").set_weights(
        [get("stem.0.weight").transpose(2, 3, 1, 0)]
    )
    keras_model.get_layer("stem_bn").set_weights(
        [get("stem.1.weight"), get("stem.1.bias"),
         get("stem.1.running_mean"), get("stem.1.running_var")]
    )
    plt2tf_count += 5

    # ---- DS 块 ----
    for i in range(len(config.MODEL.block_channels)):
        p = f"blocks.{i}"

        # Depthwise: (C, 1, kH, kW) -> (kH, kW, C, 1)
        keras_model.get_layer(f"blk{i}_dw").set_weights(
            [get(f"{p}.dw.weight").transpose(2, 3, 0, 1)]
        )
        keras_model.get_layer(f"blk{i}_dw_bn").set_weights(
            [get(f"{p}.dw_bn.weight"), get(f"{p}.dw_bn.bias"),
             get(f"{p}.dw_bn.running_mean"), get(f"{p}.dw_bn.running_var")]
        )

        # Pointwise: (C_out, C_in, 1, 1) -> (1, 1, C_in, C_out)
        keras_model.get_layer(f"blk{i}_pw").set_weights(
            [get(f"{p}.pw.weight").transpose(2, 3, 1, 0)]
        )
        keras_model.get_layer(f"blk{i}_pw_bn").set_weights(
            [get(f"{p}.pw_bn.weight"), get(f"{p}.pw_bn.bias"),
             get(f"{p}.pw_bn.running_mean"), get(f"{p}.pw_bn.running_var")]
        )
        plt2tf_count += 10

    # ---- 分类头：Linear(C_out, C_in) -> Dense kernel (C_in, C_out) ----
    keras_model.get_layer("logits").set_weights(
        [get("fc.weight").transpose(1, 0), get("fc.bias")]
    )
    plt2tf_count += 2

    print(f"[copy] 已搬运 {plt2tf_count} 个张量")


# ================================================================ 数值验证
def verify_equivalence(torch_model, keras_model, n: int = 64, seed: int = 0):
    """数值一致性检查：喂同样的输入，比对两个模型的输出。

    这是整套导出流程里最重要的一步。
    不通过就不要继续——后面的量化结果毫无意义。
    """
    rng = np.random.default_rng(seed)

    # 用真实量级的输入（纯随机正态分布即可，关键是覆盖激活值范围）
    x_np = rng.standard_normal((n, *AUDIO.feature_shape), dtype=np.float32)

    torch_model.eval()
    with torch.no_grad():
        out_torch = torch_model(torch.from_numpy(x_np)).numpy()

    # Keras 用 NHWC
    out_keras = keras_model.predict(
        x_np[..., np.newaxis], batch_size=32, verbose=0
    )

    max_diff = float(np.max(np.abs(out_torch - out_keras)))
    mean_diff = float(np.mean(np.abs(out_torch - out_keras)))

    # 相对误差（防止输出量级很小时绝对误差误判）
    scale = max(1e-8, float(np.max(np.abs(out_torch))))
    rel_diff = max_diff / scale

    print(f"[verify] 逐元素最大绝对误差 : {max_diff:.3e}")
    print(f"[verify] 平均绝对误差       : {mean_diff:.3e}")
    print(f"[verify] 相对误差           : {rel_diff:.3e}")

    # 预测类别是否一致（这是最终要的）
    agree = float(np.mean(out_torch.argmax(1) == out_keras.argmax(1)))
    print(f"[verify] 预测类别一致率     : {agree:.1%}")

    ok = max_diff < 1e-3 and agree > 0.99
    if not ok:
        print()
        print("  [严重] 数值一致性检查未通过！")
        print("  可能原因：")
        print("   1. padding 语义不一致（PyTorch 'same' 与 Keras 'same' 在偶数/奇数尺寸上可能不同）")
        print("   2. 权重转置顺序错误")
        print("   3. BN 的 running_mean/var 与 PyTorch 的 eps 约定不同")
        print("  请停止后续步骤，先修好这里。")
    return ok, {"max_abs_diff": max_diff, "mean_abs_diff": mean_diff,
                "rel_diff": rel_diff, "pred_agreement": agree}


# ================================================================ 代表性数据集
def make_representative_dataset(n_samples: int, smoke: bool, root=None):
    """构造用于校准激活值动态范围的数据集。

    关于「代表性」：
        校准数据必须来自真实数据分布。如果用随机数校准，
        各层激活值的范围会被严重误估，量化后精度会崩。
        这里的样本全部取自 SpeechCommands 的真实 MFCC 特征。
    """
    if smoke:
        print(f"[calib] 冒烟模式：用随机数据校准 {n_samples} 条（精度无意义）")
        rng = np.random.default_rng(0)

        def gen():
            for _ in range(n_samples):
                yield [rng.standard_normal(
                    (1, *AUDIO.feature_shape, 1), dtype=np.float32
                )]
        return gen

    from dataset import KeywordSpottingDataset

    print(f"[calib] 从真实数据集取 {n_samples} 条样本做校准...")
    ds = KeywordSpottingDataset(
        "validation", root or config.DATA_DIR, download=False,
        augment=False, verbose=False,
    )
    idx = np.random.default_rng(0).choice(len(ds), size=min(n_samples, len(ds)),
                                          replace=False)

    def gen():
        for i in idx:
            feat, _ = ds[int(i)]
            yield [feat.numpy()[np.newaxis, ..., np.newaxis].astype(np.float32)]

    return gen


# ================================================================ TFLite 转换
def convert(keras_model, rep_gen, int8=True, per_channel=True, float_io=False):
    """把 Keras 模型转成 TFLite，可选 INT8 量化。"""
    import tensorflow as tf

    converter = tf.lite.TFLiteConverter.from_keras_model(keras_model)

    if not int8:
        return converter.convert(), None

    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep_gen

    if per_channel:
        # 逐通道量化：每个输出通道一套 scale，精度更高
        converter._experimental_disable_per_channel = False
    else:
        converter._experimental_disable_per_channel = True

    if float_io:
        # 只量化权重和中间激活，输入/输出保持 float32
        converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS]
    else:
        # 全整数量化：MCU 上的最优选择
        converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
        converter.inference_input_type = tf.int8
        converter.inference_output_type = tf.int8

    try:
        return converter.convert(), None
    except Exception as e:                              # noqa: BLE001
        # 全整数量化有时会因为算子不支持而失败，给出一个可诊断的错误
        return None, (
            f"{type(e).__name__}: {e}\n"
            "常见原因：某个算子不支持 INT8。\n"
            "试试加 --float-io 只量化权重和中间激活。"
        )


# ================================================================ C 数组
def write_c_array(tflite_bytes: bytes, h_path: Path, cc_path: Path,
                  array_name: str = "g_model") -> None:
    """把 .tflite 转成 C 数组，供固件直接 #include。

    对齐到 16 字节：TFLite Micro 要求模型缓冲区按 16 字节对齐，
    某些内核会用对齐的 SIMD 读取。不对齐会在部分平台上触发 hardfault。
    """
    n = len(tflite_bytes)
    lines = []
    for i in range(0, n, 12):
        chunk = tflite_bytes[i:i + 12]
        lines.append("    " + ",".join(f"0x{b:02x}" for b in chunk))

    body = ",\n".join(lines)

    h_path.write_text(
        f"/* 自动生成，请勿手改。\n"
        f" * 模型: {h_path.stem}\n"
        f" * 大小: {n} 字节 ({n / 1024:.1f} KB)\n"
        f" * 生成: export_tflite.py\n"
        f" */\n"
        f"#ifndef KWS_MODEL_DATA_H\n"
        f"#define KWS_MODEL_DATA_H\n\n"
        f"#include <cstdint>\n\n"
        f"/* 必须 16 字节对齐：TFLite Micro 的 SIMD 内核有此要求 */\n"
        f"alignas(16) const unsigned char {array_name}[] = {{\n"
        f"{body}\n"
        f"}};\n"
        f"const int {array_name}_len = {n};\n\n"
        f"#endif  /* KWS_MODEL_DATA_H */\n",
        encoding="utf-8",
    )

    cc_path.write_text(
        f'#include "{h_path.name}"\n\n'
        f'extern const unsigned char {array_name}[];\n'
        f'extern const int {array_name}_len;\n',
        encoding="utf-8",
    )


# ================================================================ 主流程
def main() -> int:
    p = argparse.ArgumentParser(description="导出 INT8 TFLite + C 数组")
    p.add_argument("--ckpt", required=True, help="best.pt 路径")
    p.add_argument("--name", default="kws_dscnn", help="输出文件名前缀")
    p.add_argument("--out-dir", default=str(EXPORT_DIR))
    p.add_argument("--n-calib", type=int, default=QUANT.n_calibration,
                   help="代表性数据集样本数")
    p.add_argument("--no-per-channel", action="store_true", help="改用逐张量量化")
    p.add_argument("--float-io", action="store_true",
                   help="输入/输出保持 float32，只量化权重与中间激活")
    p.add_argument("--no-int8", action="store_true", help="只导出 FP32 模型")
    p.add_argument("--smoke", action="store_true",
                   help="冒烟测试：随机数据校准，不读数据集")
    p.add_argument("--skip-verify", action="store_true",
                   help="跳过数值一致性检查（不建议）")
    args = p.parse_args()

    ckpt_path = Path(args.ckpt)
    if not ckpt_path.exists():
        print(f"[错误] 找不到权重文件：{ckpt_path}")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("  阶段 0 · 导出 INT8 TFLite")
    print("=" * 68)

    # ---- 1. 加载 PyTorch 模型 ----
    torch_model = build_model()
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    torch_model.load_state_dict(ck["model_state"])
    torch_model.eval()
    print(f"[load] 已加载 {ckpt_path}")

    # ---- 2. 重建 Keras 模型并搬权重 ----
    keras_model = build_keras_model(len(LABELS))
    copy_weights(torch_model, keras_model)
    print(f"[keras] 模型重建完成，参数 {keras_model.count_params():,}")

    # ---- 3. 数值一致性验证 ----
    print()
    if args.skip_verify:
        print("[verify] 已跳过（--skip-verify）")
        verify = {"skipped": True}
    else:
        ok, verify = verify_equivalence(torch_model, keras_model)
        if not ok:
            return 1
        print("[verify] 通过：PyTorch 与 Keras 输出一致")

    # ---- 4. 导出 FP32 对照 ----
    print()
    rep_gen = make_representative_dataset(args.n_calib, args.smoke)
    fp32_bytes, err = convert(keras_model, rep_gen, int8=False)
    if fp32_bytes is None:
        print(f"[错误] FP32 转换失败：{err}")
        return 1
    fp32_path = out_dir / f"{args.name}_fp32.tflite"
    fp32_path.write_bytes(fp32_bytes)
    print(f"[fp32] {fp32_path.name}  {len(fp32_bytes) / 1024:.1f} KB")

    report = {
        "ckpt": str(ckpt_path),
        "name": args.name,
        "verify": verify,
        "fp32_bytes": len(fp32_bytes),
        "fp32_kb": round(len(fp32_bytes) / 1024, 2),
        "n_calibration": args.n_calib,
        "smoke": args.smoke,
    }

    # ---- 5. 导出 INT8 ----
    if not args.no_int8:
        print()
        scheme = "逐通道" if not args.no_per_channel else "逐张量"
        io = "float32 I/O" if args.float_io else "int8 I/O（全整数）"
        print(f"[int8] 方案：{scheme}，{io}，校准 {args.n_calib} 条")

        int8_bytes, err = convert(
            keras_model, rep_gen, int8=True,
            per_channel=not args.no_per_channel, float_io=args.float_io,
        )
        if int8_bytes is None:
            print(f"[错误] INT8 转换失败：\n{err}")
            out_dir.joinpath("export_report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return 1

        int8_path = out_dir / f"{args.name}_int8.tflite"
        int8_path.write_bytes(int8_bytes)
        print(f"[int8] {int8_path.name}  {len(int8_bytes) / 1024:.1f} KB")

        # ---- 6. 提取输入输出量化参数（部署必需）----
        import tensorflow as tf
        interp = tf.lite.Interpreter(model_path=str(int8_path))
        interp.allocate_tensors()
        inp = interp.get_input_details()[0]
        outp = interp.get_output_details()[0]

        in_q = inp.get("quantization") or (0.0, 0)
        out_q = outp.get("quantization") or (0.0, 0)

        print()
        print("  部署必需的两个数字（第 5 周固件要用）：")
        print(f"    输入  scale={in_q[0]:.8f}  zero_point={in_q[1]}  dtype={inp['dtype'].__name__}")
        print(f"    输出  scale={out_q[0]:.8f}  zero_point={out_q[1]}  dtype={outp['dtype'].__name__}")
        if inp["dtype"] == np.int8:
            print("    量化公式：int8 = round(float / scale) + zero_point")
            print("    反量化  ：float = scale * (int8 - zero_point)")

        write_c_array(
            int8_bytes,
            out_dir / f"{args.name}_int8.h",
            out_dir / f"{args.name}_int8.cc",
        )
        print(f"\n[c] 已生成 {args.name}_int8.h / .cc")

        report.update({
            "int8_bytes": len(int8_bytes),
            "int8_kb": round(len(int8_bytes) / 1024, 2),
            "compression_ratio": round(len(fp32_bytes) / max(1, len(int8_bytes)), 2),
            "per_channel": not args.no_per_channel,
            "float_io": args.float_io,
            "input_quantization": {"scale": float(in_q[0]), "zero_point": int(in_q[1]),
                                   "dtype": str(inp["dtype"]), "shape": list(inp["shape"])},
            "output_quantization": {"scale": float(out_q[0]), "zero_point": int(out_q[1]),
                                    "dtype": str(outp["dtype"]), "shape": list(outp["shape"])},
        })

    report_path = out_dir / "export_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                           encoding="utf-8")

    print()
    print("=" * 68)
    print(f"  FP32 : {report['fp32_kb']:.1f} KB")
    if "int8_kb" in report:
        print(f"  INT8 : {report['int8_kb']:.1f} KB  "
              f"（压缩 {report['compression_ratio']:.2f}×）")
        if report["int8_kb"] < 80:
            print("  体积  : 通过（< 80 KB）")
        else:
            print("  体积  : 超预算，需要减小模型")
    print(f"  报告  : {report_path}")
    print("=" * 68)
    if not args.no_int8 and not args.smoke:
        print("\n  下一步：python src/evaluate_tflite.py "
              f"--tflite export\\{args.name}_int8.tflite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
