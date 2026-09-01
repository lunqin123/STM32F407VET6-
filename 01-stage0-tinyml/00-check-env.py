"""
环境自检脚本

用法：
    .\\venv\\Scripts\\python.exe 00-check-env.py

它检查 6 件事，任何一项失败都会给出明确的修复建议：
    1. Python 版本
    2. torch / torchaudio 是否可用
    3. MFCC 特征形状是否符合预期（这是最容易悄悄出错的地方）
    4. 模型能否前向、参数量是否在预算内
    5. 导出工具链（tensorflow / TFLiteConverter / Keras）是否就绪
       注：本路线用「Keras 重建 + 权重搬运」，只需要 tensorflow，
       不需要 onnx / onnx2tf。详见 requirements.txt 里的说明。
    6. 磁盘空间是否够放数据集（约 3 GB）

先跑这个脚本，再去训练。省得训练到第 10 轮才发现某个环节是坏的。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

OK, WARN, FAIL = "  [ OK ]", "  [WARN ]", "  [FAIL ]"
results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    """执行一项检查，捕获异常并转成可读结论。"""
    try:
        status, msg = fn()
        results.append((name, status, msg))
    except Exception as e:                                  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {e}"))


# ------------------------------------------------------------ 1. Python
def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}（要求 >= 3.10）"


# ------------------------------------------------------------ 2. 核心依赖
def check_torch():
    import torch
    return True, f"torch {torch.__version__}（CUDA 可用: {torch.cuda.is_available()}）"


def check_torchaudio():
    import torchaudio
    return True, f"torchaudio {torchaudio.__version__}"


def check_numpy():
    import numpy
    return True, f"numpy {numpy.__version__}"


# ------------------------------------------------------------ 3. MFCC 前端
def check_mfcc():
    import torch
    import torchaudio.transforms as T

    from config import AUDIO
    wav = torch.randn(1, AUDIO.clip_samples) * 0.01
    mfcc = T.MFCC(
        sample_rate=AUDIO.sample_rate, n_mfcc=AUDIO.n_mfcc,
        melkwargs={"n_fft": AUDIO.n_fft, "hop_length": AUDIO.hop_length,
                   "n_mels": AUDIO.n_mels, "center": AUDIO.center},
    )(wav).squeeze(0)
    shape = tuple(mfcc.shape)
    want = (AUDIO.n_mfcc, AUDIO.n_frames)
    if shape != want:
        return False, f"MFCC 形状 {shape}，期望 {want}。请检查 config.AudioConfig"
    return True, f"MFCC 形状 {shape} = (n_mfcc, n_frames)，与 config 一致"


# ------------------------------------------------------------ 4. 模型
def check_model():
    import torch

    from config import AUDIO
    from model import build_model, count_parameters

    m = build_model()
    m.eval()
    with torch.no_grad():
        y = m(torch.randn(2, *AUDIO.feature_shape))
    n = count_parameters(m)
    kb = n / 1024
    ok = y.shape == (2, len(__import__("config").LABELS)) and kb < 80
    return ok, f"参数量 {n:,}，INT8 约 {kb:.1f} KB（预算 80 KB），输出 {tuple(y.shape)}"


# ------------------------------------------------------------ 5. 导出工具链
def check_export():
    """导出链路只需要 tensorflow（TFLite 转换器 + Keras 重建用）。

    刻意不检查 onnx / onnx2tf：
        本仓库不用 ONNX 中转，装它们会拖垮依赖树（详见 requirements.txt）。
    """
    missing, msgs = [], []

    try:
        import tensorflow as tf
        has_converter = hasattr(tf.lite, "TFLiteConverter")
        msgs.append(f"tensorflow {tf.__version__}"
                    f"（TFLiteConverter {'可用' if has_converter else '不可用'}）")
        if not has_converter:
            missing.append("tensorflow 缺少 tf.lite.TFLiteConverter")
    except ImportError:
        missing.append("tensorflow-cpu")
        msgs.append("未安装 tensorflow-cpu")

    try:
        import keras
        msgs.append(f"keras {keras.__version__}")
    except ImportError:
        missing.append("keras")
        msgs.append("未安装 keras（通常随 tensorflow 一起装)")

    if missing:
        msgs.append("修复：pip install tensorflow-cpu==2.21.0 "
                    "-i https://pypi.tuna.tsinghua.edu.cn/simple")
        msgs.append("缺失项：" + "、".join(missing))
        return False, "\n".join(msgs)
    return True, "，".join(msgs)


# ------------------------------------------------------------ 6. 磁盘
def check_disk():
    root = Path(__file__).resolve().parent
    total, used, free = shutil.disk_usage(root)
    free_gb = free / (1024 ** 3)
    ok = free_gb >= 5
    return ok, f"可用空间 {free_gb:.1f} GB（数据集+环境建议 >= 5 GB）"


# ------------------------------------------------------------ 7. 数据集
def check_dataset():
    from config import DATA_DIR
    archive = DATA_DIR / "SpeechCommands" / "speech_commands_v0.02"
    if not archive.is_dir():
        return True, "尚未下载（首次训练时自动下载约 2.3 GB）"
    n = sum(1 for _ in archive.rglob("*.wav"))
    ok = n > 100_000
    return ok, f"已下载 {n:,} 个 wav 文件" + ("" if ok else "（数量偏少，建议删除重下）")


def main() -> int:
    print("=" * 68)
    print("  阶段 0 · 环境自检")
    print("=" * 68)
    print()

    check("Python 版本", check_python)
    check("torch", check_torch)
    check("torchaudio", check_torchaudio)
    check("numpy", check_numpy)
    check("MFCC 前端", check_mfcc)
    check("DS-CNN 模型", check_model)
    check("导出工具链", check_export)
    check("磁盘空间", check_disk)
    check("数据集", check_dataset)

    for name, ok, msg in results:
        print(f"{OK if ok else FAIL} {name}")
        for line in msg.split("\n"):
            print(f"          {line}")

    n_fail = sum(1 for _, ok, _ in results if not ok)
    print()
    print("=" * 68)
    if n_fail == 0:
        print("  全部通过。可以运行：python src/train.py --smoke")
    else:
        print(f"  {n_fail} 项未通过，请按上面的提示修复后再继续。")
    print("=" * 68)
    print()
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
