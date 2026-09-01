"""
阶段 0 · 关键词唤醒（KWS）全局配置

设计原则：所有超参数集中在本文件。调参时只改这里，不要改模型或训练代码。
这样保证「改了什么」是可追溯的——做量化实验时这一点很重要。

术语对照（面试/读论文时会遇到）：
    KWS      Keyword Spotting，关键词唤醒
    DS-CNN   Depthwise Separable CNN，深度可分离卷积网络（MobileNet 的基础块）
    MFCC     Mel-Frequency Cepstral Coefficients，梅尔频率倒谱系数
    PTQ      Post-Training Quantization，训练后量化
    QAT      Quantization-Aware Training，量化感知训练
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 路径
# src/config.py -> 01-stage0-tinyml/
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"          # 数据集（gitignore）
RUNS_DIR = ROOT_DIR / "runs"          # 训练产物（gitignore）
EXPORT_DIR = ROOT_DIR / "export"      # 导出的 .tflite / .h（gitignore）


# ---------------------------------------------------------------- 标签体系
# 4 个关键词 + 静音 + 未知 = 6 分类
# 为什么要有 _silence_ 和 _unknown_？
#   没有 _silence_：模型会把环境噪声强行归到某个关键词，误唤醒率极高。
#   没有 _unknown_：模型只会输出 4 个关键词之一，遇到别的词必然错判。
#   真实产品里这两类的比例需要按场景调，这里是通用起点。
KEYWORDS: list[str] = ["yes", "no", "up", "down"]
SILENCE_LABEL = "_silence_"
UNKNOWN_LABEL = "_unknown_"
LABELS: list[str] = KEYWORDS + [SILENCE_LABEL, UNKNOWN_LABEL]
NUM_CLASSES = len(LABELS)


# ---------------------------------------------------------------- 音频 / 特征
@dataclass
class AudioConfig:
    """音频前端配置。这些数字决定 MCU 上要移植的预处理代码，改动成本高。"""

    sample_rate: int = 16_000        # 16 kHz，语音识别事实标准
    clip_samples: int = 16_000       # 1 秒 = 16000 采样点
    n_fft: int = 640                 # 40 ms 窗（16000 * 0.04）
    hop_length: int = 320            # 20 ms 步长（50% 重叠）
    n_mels: int = 40                 # Mel 滤波器组数量
    n_mfcc: int = 10                 # 保留前 10 个倒谱系数

    # 帧数推导（center=False，即不补零）：
    #   frames = 1 + (clip_samples - n_fft) // hop_length
    #          = 1 + (16000 - 640) // 320 = 1 + 48 = 49
    #
    # 为什么 center=False？
    #   torchaudio 默认 center=True 会在首尾各补 n_fft//2 个零，得到 51 帧。
    #   51 帧做两次 stride=2 下采样后会出现奇数尺寸，量化友好度变差。
    #   49 帧是 ARM 官方参考实现的取值，也是 MLPerf Tiny KWS 的标准输入。
    center: bool = False

    # 归一化方式：对每条样本的 490 个特征做标准化
    # 注意：这是 per-sample 的，部署时 C 代码必须实现同样的逻辑
    per_sample_norm: bool = True

    @property
    def n_frames(self) -> int:
        """特征的时间帧数，当前配置下为 49。"""
        return 1 + (self.clip_samples - self.n_fft) // self.hop_length

    @property
    def feature_shape(self) -> tuple[int, int]:
        """MFCC 特征形状 (时间帧, 系数数) = (49, 10)。"""
        return (self.n_frames, self.n_mfcc)


# ---------------------------------------------------------------- 数据
@dataclass
class DataConfig:
    """数据集构成与增强策略。"""

    dataset_url: str = "speech_commands_v0.02"   # torchaudio 内置，约 2.3 GB
    dataset_version: str = "v0.02"

    # 每个关键词的已知样本全部使用；静音和未知按需采样
    n_silence_train: int = 4_000
    n_unknown_train: int = 8_000
    n_silence_eval: int = 800
    n_unknown_eval: int = 1_600

    seed: int = 42

    # ---- 波形级增强（训练时开启）----
    # 时间平移：把语音在 1 秒窗口内随机前后挪动，模拟说话节奏差异
    max_shift_ms: int = 100
    # 背景噪声混合：从 _background_noise_ 里取噪声按随机 SNR 叠加
    # 这是 KWS 收益最大的一项增强，直接决定真实环境的误唤醒率
    noise_prob: float = 0.8
    min_snr_db: float = 0.0
    max_snr_db: float = 15.0
    # 随机音量增益
    gain_prob: float = 0.5
    min_gain: float = 0.6
    max_gain: float = 1.3

    # ---- 谱增强 SpecAugment（在 MFCC 上做）----
    spec_augment_prob: float = 0.8
    freq_mask_param: int = 4          # 最多遮 4 个 Mel 通道
    time_mask_param: int = 8          # 最多遮 8 帧（160 ms）
    n_freq_masks: int = 2
    n_time_masks: int = 2


# ---------------------------------------------------------------- 模型
@dataclass
class ModelConfig:
    """DS-CNN 结构配置。

    参数量与体积估算（当前配置）：
        conv0      5x5x1x64                    =  1,600
        4 个 DS 块  4 x (3x3 DW 576 + 1x1 PW 4096) = 18,688
        fc         64 x 6                      =    384
        ------------------------------------------------
        合计                                     20,672 参数
        INT8 权重约 20.2 KB —— 远低于 80 KB 的验收线

    为什么用深度可分离卷积？
        标准 3x3 卷积的参数量是 3*3*C_in*C_out；
        DW+PW 拆成 3*3*C_in + C_in*C_out，在本配置下是 576 + 4096 vs 36864，
        省了约 87%。更关键的是 CMSIS-NN 对 depthwise conv 有专门的汇编内核，
        在 Cortex-M4 上能拿到数倍加速——这才是选它的真正理由，省参数只是副产品。
    """

    width_multiplier: float = 1.0     # 通道缩放，调小可继续压缩
    stem_channels: int = 64           # 第一层输出通道
    block_channels: list[int] = field(default_factory=lambda: [64, 64, 64, 64])
    block_strides: list[tuple[int, int]] = field(
        default_factory=lambda: [(2, 2), (1, 1), (2, 2), (1, 1)]
    )
    dropout: float = 0.2
    # 是否在模型内部做 per-sample 归一化
    # True  -> 归一化随模型一起导出，MCU 端直接喂原始 MFCC（推荐，省事且不会忘）
    # False -> 部署时 C 代码需要自己实现归一化
    fold_norm_into_model: bool = True


# ---------------------------------------------------------------- 训练
@dataclass
class TrainConfig:
    epochs: int = 30
    batch_size: int = 128
    num_workers: int = 4              # Windows 下若报 spawn 错误，改 0
    lr: float = 3e-3
    weight_decay: float = 1e-4
    label_smoothing: float = 0.05     # 抑制过拟合，也让量化更鲁棒
    # 学习率调度：warmup + 余弦退火
    warmup_epochs: int = 3
    min_lr_ratio: float = 0.05
    grad_clip: float = 5.0
    early_stop_patience: int = 10     # 验证集 N 轮不涨就停
    eval_every: int = 1
    seed: int = 42


# ---------------------------------------------------------------- 量化导出
@dataclass
class QuantConfig:
    """INT8 导出配置。"""

    # 代表性数据集样本数：用于统计激活值的动态范围
    # 太少 -> 校准不准，掉点多；太多 -> 导出慢。200-500 是经验区间
    n_calibration: int = 300
    # 量化方案
    #   "static"  -> 全整数量化（权重+激活都 INT8），需要代表性数据集，MCU 必须用这个
    #   "dynamic" -> 权重 INT8、激活浮点的动态范围量化，体积减半但推理仍要浮点单元
    scheme: str = "static"
    # 逐通道量化（per-channel）vs 逐张量量化（per-tensor）
    # per-channel：每个输出卷积核一套 scale，精度高，CMSIS-NN 支持，默认开
    per_channel: bool = True
    # 是否同时导出 float32 版本用于对比
    also_export_float: bool = True


# ---------------------------------------------------------------- 单例
AUDIO = AudioConfig()
DATA = DataConfig()
MODEL = ModelConfig()
TRAIN = TrainConfig()
QUANT = QuantConfig()


def summary() -> dict:
    """把全部配置导出为字典，便于写进训练日志做复现。"""
    return {
        "audio": asdict(AUDIO) | {"n_frames": AUDIO.n_frames},
        "data": asdict(DATA),
        "model": asdict(MODEL),
        "train": asdict(TRAIN),
        "quant": asdict(QUANT),
        "labels": LABELS,
        "num_classes": NUM_CLASSES,
    }


def dump(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary(), indent=2, ensure_ascii=False), encoding="utf-8")
