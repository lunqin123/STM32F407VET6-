"""
模型剖析：参数量、MACs、各层体积占比、理论推理开销

用法：
    .\\venv\\Scripts\\python.exe src\\profile.py --ckpt runs\\<运行名>\\best.pt

为什么要单独剖析：
    面试里说「我的模型 20 KB、5 MMACs」比说「我的模型很小」有力得多。
    而且第 6 周的性能报告需要这些数字做基线。
    MACs 还能帮你定位瓶颈——通常是 stem 层，因为它在最大的输入尺寸上做卷积。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                         # noqa: E402
from config import AUDIO, EXPORT_DIR  # noqa: E402
from model import build_model         # noqa: E402


class MACCounter:
    """用 forward hook 统计乘加运算数（MACs）。

    统计口径：1 MAC = 一次乘加（乘 + 加各算一次浮点运算 = 2 FLOPs）。
    行业惯例报 MACs，所以这里也报 MACs。

    为什么不用 thop / ptflops？
        它们经常跟不上 PyTorch 版本，而且对小众层支持不全。
        自己用 hook 写 30 行，可控、可解释，出了问题自己能改。
    """

    def __init__(self, model: nn.Module):
        self.total = 0
        self.per_layer: dict[str, int] = {}
        self._hooks = []
        for name, m in model.named_modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                self._hooks.append(m.register_forward_hook(self._hook(name)))

    def _hook(self, name: str):
        def fn(module, inp, out):
            if isinstance(module, nn.Conv2d):
                # 输出每个空间位置需要 kH*kW*Cin/groups 次 MAC
                _, c_in, h, w = inp[0].shape
                kh, kw = module.kernel_size
                groups = module.groups
                macs_per_pixel = (kh * kw * c_in) // groups
                n_positions = out.shape[2] * out.shape[3]
                n_filters = module.out_channels
                macs = macs_per_pixel * n_positions * n_filters
            else:  # Linear
                macs = module.in_features * module.out_features * inp[0].shape[0]
                macs = macs // max(1, inp[0].shape[0])     # 单样本口径
            self.per_layer[name] = macs
            self.total += macs
        return fn

    def close(self):
        for h in self._hooks:
            h.remove()


def main() -> int:
    p = argparse.ArgumentParser(description="剖析 KWS 模型的参数量与算力")
    p.add_argument("--ckpt", default=None)
    p.add_argument("--device", default="cpu")
    args = p.parse_args()

    model = build_model().to(args.device)
    if args.ckpt:
        ck = Path(args.ckpt)
        if not ck.exists():
            print(f"[错误] 找不到 {ck}")
            return 1
        sd = torch.load(ck, map_location=args.device, weights_only=False)["model_state"]
        model.load_state_dict(sd)
        print(f"[model] 已加载 {ck}")
    model.eval()

    dummy = torch.randn(1, *AUDIO.feature_shape).to(args.device)
    counter = MACCounter(model)
    with torch.no_grad():
        model(dummy)
    counter.close()

    total_params = sum(p.numel() for p in model.parameters())
    conv_params = sum(
        p.numel() for m in model.modules() if isinstance(m, nn.Conv2d)
        for p in m.parameters()
    )
    bn_params = sum(
        p.numel() for m in model.modules() if isinstance(m, nn.BatchNorm2d)
        for p in m.parameters()
    )
    fc_params = sum(
        p.numel() for m in model.modules() if isinstance(m, nn.Linear)
        for p in m.parameters()
    )

    macs = counter.total
    print("\n" + "=" * 66)
    print("  模型剖析报告")
    print("=" * 66)
    print(f"  输入形状            : (1, {AUDIO.n_frames}, {AUDIO.n_mfcc})")
    print(f"  总参数量            : {total_params:,}")
    print(f"    ├ 卷积            : {conv_params:,}  ({conv_params / total_params:.1%})")
    print(f"    ├ BatchNorm       : {bn_params:,}  ({bn_params / total_params:.1%})   [量化时会被折叠]")
    print(f"    └ 全连接          : {fc_params:,}  ({fc_params / total_params:.1%})")
    print()
    print(f"  算力                : {macs / 1e6:.2f} MMACs  （= {2 * macs / 1e6:.2f} MFLOPs）")
    print()
    print("  各层 MACs 占比")
    print("  " + "-" * 62)
    for name, v in sorted(counter.per_layer.items(), key=lambda kv: -kv[1]):
        bar = "#" * int(30 * v / max(counter.per_layer.values()))
        print(f"    {name:<22}{v / 1e6:>8.3f}M{v / macs:>8.1%}  {bar}")
    print()
    print("  体积估算")
    print("  " + "-" * 62)
    print(f"    FP32 权重         : {total_params * 4 / 1024:>8.1f} KB")
    print(f"    INT8 权重         : {total_params / 1024:>8.1f} KB   （验收线 < 80 KB）")
    print(f"    INT8（折叠 BN 后）: {(conv_params + fc_params) / 1024:>8.1f} KB   ← 实际部署体积")
    print()
    print("  推理耗时粗估（STM32F407 @168 MHz，CMSIS-NN）")
    print("  " + "-" * 62)
    # 经验值：Cortex-M4 上优化过的 int8 卷积约 1.5-3 MAC/cycle
    for eff in (1.5, 3.0):
        cycles = macs / eff
        print(f"    假设 {eff:.1f} MAC/cycle : {cycles / 168e6 * 1000:>7.2f} ms")
    print("  注：这是纯算力下界，实际还要加上内存搬运、MFCC 特征提取（约 3-5 ms）")
    print("      和归一化开销。验收线是 < 30 ms。")
    print("=" * 66 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
