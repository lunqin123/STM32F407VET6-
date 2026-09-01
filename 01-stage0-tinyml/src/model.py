"""
DS-CNN：深度可分离卷积关键词唤醒模型

结构（输入 1 x 49 x 10 的 MFCC）：

    Input  (1, 49, 10)
      |
    Conv2d 5x5, 1->64, stride (2,2)   -> (64, 25, 5)
      |
    DSBlock stride (2,2)              -> (64, 13, 3)
    DSBlock stride (1,1)              -> (64, 13, 3)
    DSBlock stride (2,2)              -> (64,  7, 2)
    DSBlock stride (1,1)              -> (64,  7, 2)
      |
    AdaptiveAvgPool2d -> flatten      -> (64,)
    Dropout -> Linear(64, 6)          -> (6,)

为量化做的三个刻意设计（很重要，改动前先读懂）：

1. 只用了 Conv2d / BatchNorm / ReLU / AvgPool / Linear。
   没有 SE 注意力、没有 LayerNorm、没有 GELU、没有动态形状操作。
   原因：TFLite Micro 的算子覆盖是有限的，花哨的算子会直接转换失败，
   或者在 MCU 上退化成慢到不能用的参考实现。

2. ReLU 而不是 ReLU6 / HardSwish。
   ReLU 的激活范围是无上界的，量化时靠代表性数据集校准确定范围；
   ReLU6 人为截断在 [0,6]，看似更适合量化，但它会压缩有用动态范围，
   在小模型上实测掉点更多。MobileNet 用 ReLU6 是因为它的通道数多。

3. 归一化折叠进模型（fold_norm_into_model=True）。
   per-sample 标准化本来是预处理步骤，如果在模型外做，部署时很容易忘记实现，
   导致「PC 上 96%、板子上 60%」这种最典型的坑。放进模型里就能随图一起导出。
"""

from __future__ import annotations

import torch
import torch.nn as nn

from config import MODEL, NUM_CLASSES


class DSBlock(nn.Module):
    """深度可分离卷积块 = Depthwise(3x3) + Pointwise(1x1)。

    标准卷积 3x3 的参数量：   3 * 3 * C_in * C_out
    深度可分离的参数量：      3 * 3 * C_in  +  C_in * C_out

    以 64->64 为例：36,864 -> 4,672，省 87%。
    更重要的是 CMSIS-NN 有专门的 arm_depthwise_conv_* 内核，
    在 Cortex-M4 上比通用 GEMM 快得多。
    """

    def __init__(self, in_ch: int, out_ch: int, stride: tuple[int, int]):
        super().__init__()
        # groups=in_ch 让它变成 depthwise：每个输入通道被自己的卷积核处理
        self.dw = nn.Conv2d(
            in_ch, in_ch, kernel_size=3, stride=stride,
            padding=1, groups=in_ch, bias=False,
        )
        self.dw_bn = nn.BatchNorm2d(in_ch)
        self.pw = nn.Conv2d(in_ch, out_ch, kernel_size=1, bias=False)
        self.pw_bn = nn.BatchNorm2d(out_ch)
        self.relu = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.relu(self.dw_bn(self.dw(x)))
        x = self.relu(self.pw_bn(self.pw(x)))
        return x


class PerSampleNorm(nn.Module):
    """对每条样本的全部特征做标准化： (x - mean) / (std + eps)。

    为什么是 per-sample 而不是用训练集统计的全局 mean/std？
        麦克风增益、说话人音量、环境本底噪声都会整体平移 MFCC 的数值。
        per-sample 归一化能抵消这种整体偏移，鲁棒性明显更好。
    代价：
        MCU 上要额外算一遍 490 个点的均值方差（约 1000 次浮点运算，<0.1 ms）。
        放在模型里可以让它随图一起导出，避免「忘了实现」的经典事故。
    """

    def __init__(self, eps: float = 1e-5):
        super().__init__()
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, T, F)
        dims = (1, 2, 3)
        mean = x.mean(dim=dims, keepdim=True)
        var = x.var(dim=dims, unbiased=False, keepdim=True)
        return (x - mean) / torch.sqrt(var + self.eps)


class DSCNN(nn.Module):
    """深度可分离 CNN 关键词分类器。"""

    def __init__(self, num_classes: int = NUM_CLASSES, cfg=MODEL, fold_norm: bool | None = None):
        super().__init__()

        if fold_norm is None:
            fold_norm = cfg.fold_norm_into_model
        self.fold_norm = fold_norm
        if fold_norm:
            self.norm = PerSampleNorm()

        w = cfg.width_multiplier

        def ch(x: int) -> int:
            # 通道数对齐到 8 的倍数：CMSIS-NN 的 SIMD 内核按 8 通道一组处理，
            # 不对齐会落到慢速路径上。这是一个不影响精度但影响推理速度的取舍。
            return max(8, int(x * w) // 8 * 8)

        stem = ch(cfg.stem_channels)
        self.stem = nn.Sequential(
            nn.Conv2d(1, stem, kernel_size=5, stride=(2, 2), padding=(2, 2), bias=False),
            nn.BatchNorm2d(stem),
            nn.ReLU(),
        )

        blocks: list[nn.Module] = []
        in_ch = stem
        for out_ch_raw, stride in zip(cfg.block_channels, cfg.block_strides):
            out_ch = ch(out_ch_raw)
            blocks.append(DSBlock(in_ch, out_ch, stride))
            in_ch = out_ch
        self.blocks = nn.Sequential(*blocks)
        self.last_channels = in_ch

        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.dropout = nn.Dropout(cfg.dropout)
        self.fc = nn.Linear(self.last_channels, num_classes)

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, std=0.01)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 3:                 # (B, T, F) -> (B, 1, T, F)
            x = x.unsqueeze(1)
        if self.fold_norm:
            x = self.norm(x)
        x = self.stem(x)
        x = self.blocks(x)
        return x

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.forward_features(x)
        x = self.pool(x).flatten(1)
        x = self.dropout(x)
        return self.fc(x)

    def input_shape(self) -> tuple[int, int, int]:
        """模型期望的输入形状 (C, T, F)，导出和部署时都要用。"""
        return (1, *__import__("config").AUDIO.feature_shape)


def build_model(num_classes: int = NUM_CLASSES) -> DSCNN:
    return DSCNN(num_classes=num_classes)


def count_parameters(model: nn.Module, trainable_only: bool = True) -> int:
    if trainable_only:
        return sum(p.numel() for p in model.parameters() if p.requires_grad)
    return sum(p.numel() for p in model.parameters())


if __name__ == "__main__":
    import config

    m = build_model()
    print(m)
    print()
    print(f"参数量            : {count_parameters(m):,}")
    print(f"输入形状 (C,T,F)  : {m.input_shape()}")
    print(f"INT8 权重体积估算  : {count_parameters(m) / 1024:.1f} KB（上限，BN 会被折叠）")

    # 前向一次，确认形状推导无误
    dummy = torch.randn(2, *config.AUDIO.feature_shape)
    with torch.no_grad():
        y = m(dummy)
    print(f"输出形状          : {tuple(y.shape)}  (batch=2, n_classes={y.shape[1]})")
