"""
评估导出的 TFLite 模型（INT8 / FP32 都支持），并可选与 PyTorch FP32 逐样本对比

用法（在 01-stage0-tinyml 目录下）：

    # 单独评估 INT8 模型
    python src\\evaluate_tflite.py --tflite export\\kws_dscnn_int8.tflite

    # 关键用法：和 FP32 原始模型跑同一批样本，直接量化出掉点
    python src\\evaluate_tflite.py --tflite export\\kws_dscnn_int8.tflite ^
           --compare-ckpt runs\\<运行名>\\best.pt

    # 跑 FP32 的 tflite 作为对照，排除「转换」引入的误差
    python src\\evaluate_tflite.py --tflite export\\kws_dscnn_fp32.tflite

    # 冒烟测试：不下载数据集
    python src\\evaluate_tflite.py --tflite export\\kws_dscnn_int8.tflite --smoke

    # 对比两种量化方案
    python src\\evaluate_tflite.py --tflite export\\int8_pertensor.tflite --name per_tensor
    python src\\evaluate_tflite.py --tflite export\\int8_perchan.tflite  --name per_channel

================================================================================
为什么必须有这个脚本
================================================================================

「模型体积从 85 KB 压到 21 KB」是一个很容易让人满意的指标，但它不能告诉你
模型还能不能用。真正决定能不能上线的是：

    1. INT8 之后准确率掉了多少？（可接受范围：绝对掉点 < 3%）
    2. 如果掉了，掉在哪一类？
       实践中几乎总是 _unknown_ 先崩 —— 因为 unknown 是 20 个词的混合体，
       激活值分布最分散，量化范围最难估准。
    3. 有多少样本的预测结果「翻转」了？（agreement）
       即使总准确率只掉 1%，如果翻转率有 8%，说明模型处在决策边界附近很不稳定，
       换个麦克风、换个环境就会露馅。

另外这里还做了一个容易被忽略的检查：**输入饱和率**。
如果真实 MFCC 的数值范围超出了校准时确定的 [scale, zero_point] 量程，
超出的部分会被硬截断到 -128 / 127，信息直接丢失，而且是无声无息的。
脚本会统计被截断的元素比例，超过 0.1% 就应该增加校准样本数重导。

================================================================================
INT8 输入输出的换算（部署时固件里要写同样这段）
================================================================================

    量化   int8  = clip(round(float / scale) + zero_point, -128, 127)
    反量化 float = scale * (int8 - zero_point)

注意输出也是 int8 的 logits。虽然不同类别共享同一个 scale（逐张量），
所以 argmax 在 int8 域上算和先反量化再算是等价的；
但要算 softmax 置信度，就必须先反量化，否则数值完全不对。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                            # noqa: E402
from config import AUDIO, LABELS, SILENCE_LABEL, UNKNOWN_LABEL  # noqa: E402


# ================================================================ 指标计算
def confusion_matrix(preds: np.ndarray, targets: np.ndarray, n: int) -> np.ndarray:
    """行=真值，列=预测。与 evaluate.py 保持一致，不依赖 sklearn。"""
    cm = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(targets, preds):
        cm[t, p] += 1
    return cm


def print_confusion_matrix(cm: np.ndarray, labels: list[str]) -> None:
    width = max(9, max(len(x) for x in labels) + 1)
    header = "真值\\预测".ljust(width) + "".join(x[:width - 1].rjust(width) for x in labels)
    print("\n混淆矩阵（行=真值，列=预测）")
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for i, row in enumerate(cm):
        line = labels[i].ljust(width)
        for j, v in enumerate(row):
            line += (f"[{v}]" if i == j else f"{v}").rjust(width)
        print(line)
    print("-" * len(header))


def per_class_report(cm: np.ndarray, labels: list[str]) -> list[dict]:
    rows = []
    for i, name in enumerate(labels):
        tp = int(cm[i, i])
        fn = int(cm[i, :].sum() - tp)
        fp = int(cm[:, i].sum() - tp)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append({
            "class": name, "support": int(cm[i, :].sum()),
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "false_positives": fp, "false_negatives": fn,
        })
    return rows


def softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def business_metrics(cm: np.ndarray) -> dict:
    sil_i = LABELS.index(SILENCE_LABEL)
    unk_i = LABELS.index(UNKNOWN_LABEL)
    kw_idx = [LABELS.index(k) for k in config.KEYWORDS]

    kw_recall = float(np.mean([cm[i, i] / cm[i, :].sum() for i in kw_idx]))
    neg_total = cm[sil_i, :].sum() + cm[unk_i, :].sum()
    fa = float((neg_total - cm[sil_i, sil_i] - cm[unk_i, unk_i]) / max(1, neg_total))
    return {"keyword_recall": kw_recall, "false_accept_rate": fa}


# ================================================================ TFLite 推理
class TFLiteRunner:
    """包装 tf.lite.Interpreter，处理 INT8 的输入量化与输出反量化。

    为什么自己写这个包装：
        interpreter 的 invoke() 只接受与 input_details 完全一致的 dtype 和 shape。
        直接把 float32 的 MFCC 塞给 int8 模型会报类型错误，
        而手动量化又容易忘记 clip 和 round 的顺序。集中在一处就不会错。
    """

    def __init__(self, model_path: Path, num_threads: int = 4):
        import tensorflow as tf

        self.interp = tf.lite.Interpreter(
            model_path=str(model_path), num_threads=num_threads
        )
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]
        self.outp = self.interp.get_output_details()[0]

        self.in_scale, self.in_zp = self._q(self.inp)
        self.out_scale, self.out_zp = self._q(self.outp)
        self.input_dtype = self.inp["dtype"]
        self.input_is_int8 = self.input_dtype == np.int8

        # 统计输入饱和（被 clip 掉）的元素比例
        self._clipped = 0
        self._total_elems = 0

    @staticmethod
    def _q(detail: dict) -> tuple[float, int]:
        q = detail.get("quantization") or (0.0, 0)
        return (float(q[0]) if q[0] else 0.0, int(q[1]))

    def _prepare(self, feat: np.ndarray) -> np.ndarray:
        """feat: (T, F) float32 -> 模型需要的 (1, T, F, 1)，按需量化为 int8。"""
        x = feat.astype(np.float32)[np.newaxis, ..., np.newaxis]

        if not self.input_is_int8:
            return x

        if self.in_scale == 0.0:
            raise RuntimeError(
                "模型声明为 int8 输入，但读不到 scale/zero_point。"
                "这通常是导出时用了 --float-io，或转换器版本异常。"
            )

        scaled = x / self.in_scale + self.in_zp
        # 先统计饱和，再 clip —— 顺序不能反，否则统计永远是 0
        self._clipped += int(np.sum((scaled < -128) | (scaled > 127)))
        self._total_elems += int(scaled.size)
        return np.clip(np.round(scaled), -128, 127).astype(np.int8)

    def __call__(self, feat: np.ndarray) -> np.ndarray:
        """返回反量化后的 float32 logits，形状 (n_classes,)。"""
        self.interp.set_tensor(self.inp["index"], self._prepare(feat))
        self.interp.invoke()
        out = self.interp.get_tensor(self.outp["index"])[0]

        if out.dtype == np.int8 or out.dtype == np.uint8:
            if self.out_scale:
                out = self.out_scale * (out.astype(np.float32) - self.out_zp)
            else:
                out = out.astype(np.float32)
        return out.astype(np.float32)

    @property
    def clip_ratio(self) -> float:
        return self._clipped / max(1, self._total_elems)


# ================================================================ 数据准备
def load_samples(split: str, limit: int, smoke: bool, data_root: str):
    """返回 (features, labels)，features 为 float32 数组 (N, T, F)。"""
    if smoke:
        from dataset import SyntheticKWSDataset

        n = limit if limit > 0 else 256
        ds = SyntheticKWSDataset(n_samples=n, seed=0)
        print(f"[data] 冒烟模式：合成数据 {n} 条（精度无意义，只验证流程）")
    else:
        from dataset import KeywordSpottingDataset

        ds = KeywordSpottingDataset(
            split, Path(data_root), download=True, augment=False, verbose=False
        )
        print(f"[data] {split} 集：{len(ds):,} 条")

    indices = list(range(len(ds)))
    if limit > 0 and limit < len(indices):
        rng = np.random.default_rng(0)
        indices = sorted(rng.choice(len(ds), size=limit, replace=False).tolist())

    feats = np.stack([ds[i][0].numpy().astype(np.float32) for i in indices])
    labels = np.array([ds[i][1] for i in indices], dtype=np.int64)
    return feats, labels, len(indices)


# ================================================================ 主流程
def main() -> int:
    p = argparse.ArgumentParser(description="评估 TFLite 模型")
    p.add_argument("--tflite", required=True, help=".tflite 文件路径")
    p.add_argument("--split", default="testing", choices=["validation", "testing"])
    p.add_argument("--limit", type=int, default=0, help="最多评估多少条，0=全部")
    p.add_argument("--smoke", action="store_true", help="用合成数据，不读数据集")
    p.add_argument("--compare-ckpt", default=None,
                   help="PyTorch FP32 权重路径，用于逐样本对比量化掉点")
    p.add_argument("--data-root", default=str(config.DATA_DIR))
    p.add_argument("--num-threads", type=int, default=4)
    p.add_argument("--name", default=None, help="报告里的方案名，便于多方案对比")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    tflite_path = Path(args.tflite)
    if not tflite_path.exists():
        print(f"[错误] 找不到模型文件：{tflite_path}")
        print("       先跑：python src\\export_tflite.py --ckpt runs\\<运行名>\\best.pt")
        return 1

    print("=" * 68)
    print("  阶段 0 · TFLite 模型评估")
    print("=" * 68)

    runner = TFLiteRunner(tflite_path, num_threads=args.num_threads)
    tag = args.name or tflite_path.stem

    print(f"[model] {tflite_path.name}")
    print(f"[model] 输入 dtype={runner.input_dtype.__name__}  "
          f"shape={tuple(runner.inp['shape'])}")
    if runner.input_is_int8:
        print(f"[model] 输入 scale={runner.in_scale:.8f}  zero_point={runner.in_zp}")
    print(f"[model] 输出 scale={runner.out_scale:.8f}  "
          f"zero_point={runner.out_zp}  dtype={runner.outp['dtype'].__name__}")
    print(f"[model] 文件大小 {tflite_path.stat().st_size / 1024:.1f} KB")

    # ---- 数据 ----
    feats, targets, n = load_samples(args.split, args.limit, args.smoke, args.data_root)
    print(f"[data]  实际评估 {n:,} 条")

    # ---- TFLite 推理 ----
    print("\n[infer] 逐样本推理中...")
    logits = np.zeros((n, len(LABELS)), dtype=np.float32)
    t0 = time.perf_counter()
    for i in range(n):
        logits[i] = runner(feats[i])
    elapsed = time.perf_counter() - t0
    per_sample_ms = elapsed / n * 1000.0

    preds = logits.argmax(1)
    probs = softmax(logits)
    acc = float((preds == targets).mean())

    print(f"[infer] 完成：{elapsed:.2f} s，单样本 {per_sample_ms:.2f} ms"
          f"（PC CPU，含 Python 调用开销，不代表 MCU 性能）")

    # ---- 报告 ----
    cm = confusion_matrix(preds, targets, len(LABELS))
    rows = per_class_report(cm, LABELS)
    biz = business_metrics(cm)
    conf = probs.max(axis=1)

    print("\n" + "=" * 68)
    print(f"  方案 {tag}   |   数据集 {args.split}   样本 {n:,}")
    print(f"  总准确率: {acc:.2%}")
    print("=" * 68)

    print_confusion_matrix(cm, LABELS)

    print("\n逐类指标")
    print(f"{'类别':<12}{'样本':>8}{'精确率':>10}{'召回率':>10}{'F1':>10}{'误报':>8}{'漏报':>8}")
    print("-" * 66)
    for r in rows:
        print(f"{r['class']:<12}{r['support']:>8}{r['precision']:>10.2%}"
              f"{r['recall']:>10.2%}{r['f1']:>10.2%}"
              f"{r['false_positives']:>8}{r['false_negatives']:>8}")

    print("\n业务指标")
    print("-" * 66)
    print(f"  关键词平均召回率   : {biz['keyword_recall']:.2%}   （目标 > 95%）")
    print(f"  负样本误接受率 FA  : {biz['false_accept_rate']:.2%}   （目标 < 5%）")
    print(f"  预测置信度         : 均值 {conf.mean():.3f}  中位 {np.median(conf):.3f}")
    print(f"  输入饱和率         : {runner.clip_ratio:.4%}")
    if runner.clip_ratio > 0.001:
        print("  [警告] 有较多输入元素超出量程被截断，量化会丢信息。")
        print("         建议：增大 --n-calib 重新导出（比如 1000 条）。")
    elif runner.input_is_int8:
        print("  （饱和率 < 0.1%，校准范围合适）")

    report: dict = {
        "tflite": str(tflite_path), "name": tag, "split": args.split,
        "n_samples": n, "accuracy": acc,
        "keyword_recall": biz["keyword_recall"],
        "false_accept_rate": biz["false_accept_rate"],
        "per_class": rows, "confusion_matrix": cm.tolist(),
        "confidence": {"mean": float(conf.mean()),
                       "median": float(np.median(conf))},
        "input_clip_ratio": runner.clip_ratio,
        "input_quantization": {"scale": runner.in_scale,
                               "zero_point": runner.in_zp,
                               "dtype": str(runner.input_dtype)},
        "output_quantization": {"scale": runner.out_scale,
                                "zero_point": runner.out_zp},
        "pc_latency_ms_per_sample": round(per_sample_ms, 3),
        "smoke": args.smoke,
    }

    # ---- 与 FP32 对比 ----
    if args.compare_ckpt:
        import torch
        from model import build_model

        ckpt_path = Path(args.compare_ckpt)
        if not ckpt_path.exists():
            print(f"\n[警告] 找不到对比权重 {ckpt_path}，跳过对比")
        else:
            model = build_model()
            ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
            model.load_state_dict(ck["model_state"])
            model.eval()

            with torch.no_grad():
                fp_logits = model(torch.from_numpy(feats)).numpy()
            fp_preds = fp_logits.argmax(1)
            fp_probs = softmax(fp_logits)
            fp_acc = float((fp_preds == targets).mean())
            fp_cm = confusion_matrix(fp_preds, targets, len(LABELS))
            fp_biz = business_metrics(fp_cm)

            agree = float((fp_preds == preds).mean())
            drop = fp_acc - acc

            print("\n" + "=" * 68)
            print("  量化前后对比（同一批样本）")
            print("=" * 68)
            print(f"{'指标':<20}{'FP32':>14}{'TFLite':>14}{'变化':>14}")
            print("-" * 62)
            print(f"{'总准确率':<20}{fp_acc:>14.2%}{acc:>14.2%}{drop:>+14.2%}")
            print(f"{'关键词召回':<20}{fp_biz['keyword_recall']:>14.2%}"
                  f"{biz['keyword_recall']:>14.2%}"
                  f"{fp_biz['keyword_recall'] - biz['keyword_recall']:>+14.2%}")
            print(f"{'误接受率 FA':<20}{fp_biz['false_accept_rate']:>14.2%}"
                  f"{biz['false_accept_rate']:>14.2%}"
                  f"{biz['false_accept_rate'] - fp_biz['false_accept_rate']:>+14.2%}")
            print(f"{'平均置信度':<20}{fp_probs.max(1).mean():>14.3f}"
                  f"{conf.mean():>14.3f}"
                  f"{conf.mean() - fp_probs.max(1).mean():>+14.3f}")
            print("-" * 62)
            print(f"  预测结果一致率 : {agree:.2%}"
                  f"（{int(round(agree * n)):,}/{n:,} 条结果相同）")

            print("\n判定：")
            if drop < 0.01:
                print("  [通过] 掉点 < 1%，量化质量良好。")
            elif drop < 0.03:
                print("  [可接受] 掉点 1%~3%。若 FA 同时上升，优先加校准样本数。")
            else:
                print("  [不合格] 掉点 > 3%。按下面顺序排查：")
                print("    1. 先跑 FP32 的 .tflite（不加 --compare-ckpt），")
                print("       如果 FP32 tflite 也掉点，问题在「转换」不在「量化」")
                print("    2. 增大校准样本数到 1000 重新导出")
                print("    3. 输入饱和率高说明校准数据不 representative")
                print("    4. 以上都试过仍掉点，才考虑 QAT（量化感知训练）")

            if agree < 0.95 and drop < 0.01:
                print("\n  [注意] 总准确率掉点小，但预测翻转较多。")
                print("         说明大量样本处在决策边界，实际部署会不稳定。")

            report["comparison"] = {
                "fp32_ckpt": str(ckpt_path),
                "fp32_accuracy": fp_acc,
                "accuracy_drop": drop,
                "prediction_agreement": agree,
                "fp32_keyword_recall": fp_biz["keyword_recall"],
                "fp32_false_accept_rate": fp_biz["false_accept_rate"],
                "fp32_confidence_mean": float(fp_probs.max(1).mean()),
            }

    out_path = Path(args.out) if args.out else tflite_path.parent / f"eval_{tag}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  报告已保存：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
