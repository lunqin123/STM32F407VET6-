"""
数据集：Google SpeechCommands v0.02 + MFCC 前端 + 增强

数据流：
    原始 wav (16 kHz, 1 s)
      -> [训练时] 随机平移 / 加噪 / 变音量
      -> MFCC (n_mels=40 -> n_mfcc=10, 49 帧)
      -> [训练时] SpecAugment 时频遮挡
      -> (49, 10) 张量

关于数据集：
    SpeechCommands v0.02 共 105,829 条 1 秒语音，35 个单词，约 2.3 GB。
    我们只用 4 个关键词的全部样本，加上从其余 31 个词里采样的 _unknown_，
    以及用背景噪声合成的 _silence_。
    划分严格遵循官方 validation_list.txt / testing_list.txt，不存在数据泄漏。

第一次运行会自动下载约 2.3 GB，请预留时间。
"""

from __future__ import annotations

import random
from pathlib import Path

import torch
import torch.nn.functional as F
import torchaudio
import torchaudio.transforms as T
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm

import config
from config import (
    AUDIO, DATA, DATA_DIR, KEYWORDS, LABELS, SILENCE_LABEL, UNKNOWN_LABEL,
)

# 背景噪声目录名（官方数据集里自带）
BACKGROUND_NOISE = "_background_noise_"


# ================================================================ 工具函数
def _pad_or_crop(wav: torch.Tensor, target: int, start: int | None = None) -> torch.Tensor:
    """把波形补零/裁剪到 target 长度。

    start=None 时居中放置；否则从指定位置开始。
    """
    n = wav.shape[-1]
    if n >= target:
        s = (n - target) // 2 if start is None else min(start, n - target)
        return wav[..., s:s + target]
    out = wav.new_zeros(target)
    s = (target - n) // 2 if start is None else start
    out[s:s + n] = wav
    return out


def _mix_at_snr(speech: torch.Tensor, noise: torch.Tensor, snr_db: float) -> torch.Tensor:
    """按给定信噪比把噪声叠加到语音上。

    SNR(dB) = 10 * log10(P_speech / P_noise)
    => scale = sqrt(P_speech / (P_noise * 10^(SNR/10)))
    """
    p_speech = speech.pow(2).sum()
    p_noise = noise.pow(2).sum().clamp(min=1e-12)
    scale = torch.sqrt(p_speech / (p_noise * (10 ** (snr_db / 10.0))))
    return speech + scale * noise


def _random_noise_slice(noise_store: list[torch.Tensor], length: int, rng: random.Random):
    """从噪声库里随机取一段。噪声文件比 1 s 长时随机取窗，短时补零。"""
    wav = noise_store[rng.randrange(len(noise_store))]
    n = wav.shape[-1]
    if n <= length:
        return _pad_or_crop(wav, length, start=None)
    start = rng.randrange(0, n - length)
    return wav[start:start + length]


def _load_background_noise(root: Path) -> list[torch.Tensor]:
    """加载 _background_noise_ 目录下所有噪声文件。"""
    noise_dir = root / BACKGROUND_NOISE
    if not noise_dir.is_dir():
        raise FileNotFoundError(
            f"未找到背景噪声目录：{noise_dir}\n"
            "数据集可能未完整下载。请删除 data/ 目录后重新运行一次下载。"
        )
    store: list[torch.Tensor] = []
    for p in sorted(noise_dir.glob("*.wav")):
        wav, sr = torchaudio.load(str(p))
        if sr != AUDIO.sample_rate:
            wav = torchaudio.functional.resample(wav, sr, AUDIO.sample_rate)
        if wav.shape[0] > 1:                       # 多声道取平均
            wav = wav.mean(dim=0, keepdim=True)
        store.append(wav.squeeze(0))
    if not store:
        raise FileNotFoundError(f"{noise_dir} 下没有 .wav 文件")
    return store


# ================================================================ 数据集
class KeywordSpottingDataset(Dataset):
    """按关键词筛选后的 SpeechCommands 子集。

    split: "training" | "validation" | "testing"
    """

    def __init__(
        self,
        split: str,
        root: Path | str = DATA_DIR,
        download: bool = True,
        augment: bool | None = None,
        verbose: bool = True,
        seed: int | None = None,
    ):
        if split not in ("training", "validation", "testing"):
            raise ValueError(f"split 必须是 training/validation/testing，收到 {split!r}")

        self.split = split
        self.root = Path(root)
        self.augment = (split == "training") if augment is None else augment
        self.rng = random.Random(seed if seed is not None else DATA.seed)
        self.verbose = verbose

        if verbose:
            print(f"[data] 加载 SpeechCommands（split={split}）...")
        try:
            base = torchaudio.datasets.SPEECHCOMMANDS(
                root=str(self.root), url=DATA.dataset_url,
                folder_in_archive="SpeechCommands",
                download=download, subset=split,
            )
        except RuntimeError as e:
            raise RuntimeError(
                "数据集加载失败。常见原因：\n"
                "  1. 网络中断导致下载不完整 —— 删除 data/ 重跑\n"
                "  2. 磁盘空间不足（需要约 3 GB 空闲）\n"
                f"原始错误：{e}"
            ) from e

        archive = self.root / "SpeechCommands" / "speech_commands_v0.02"

        # --- 按单词归类所有样本路径 ---
        by_word: dict[str, list[Path]] = {}
        for p in base._walker:
            by_word.setdefault(Path(p).parent.name, []).append(Path(p))

        if verbose:
            print(f"[data] 共 {sum(len(v) for v in by_word.values()):,} 条样本，"
                  f"{len(by_word)} 个类别")

        # --- 保存噪声库（静音样本与加噪增强都要用）---
        self.noise_store = _load_background_noise(archive)

        # --- 组装样本清单：(path_or_None, label_index) ---
        # path 为 None 表示该样本是合成的静音
        items: list[tuple[Path | None, int]] = []

        # 1) 四个关键词：全量使用
        for w in KEYWORDS:
            files = by_word.get(w, [])
            if not files:
                raise RuntimeError(f"数据集中找不到关键词 {w!r}，请检查下载是否完整")
            items += [(f, LABELS.index(w)) for f in files]

        # 2) _unknown_：从其余单词里随机采样
        others = [w for w in by_word
                  if w not in KEYWORDS and w != BACKGROUND_NOISE]
        n_unknown = DATA.n_unknown_train if split == "training" else DATA.n_unknown_eval
        other_files = [(f, w) for w in others for f in by_word[w]]
        if n_unknown > 0 and other_files:
            picked = self.rng.sample(other_files, min(n_unknown, len(other_files)))
            items += [(f, LABELS.index(UNKNOWN_LABEL)) for f, _ in picked]

        # 3) _silence_：由背景噪声合成，路径为 None
        n_silence = DATA.n_silence_train if split == "training" else DATA.n_silence_eval
        items += [(None, LABELS.index(SILENCE_LABEL)) for _ in range(n_silence)]

        self.rng.shuffle(items)
        self.items = items
        self.by_word = by_word

        # --- MFCC 前端 ---
        self.mfcc = T.MFCC(
            sample_rate=AUDIO.sample_rate,
            n_mfcc=AUDIO.n_mfcc,
            melkwargs={
                "n_fft": AUDIO.n_fft,
                "hop_length": AUDIO.hop_length,
                "n_mels": AUDIO.n_mels,
                "center": AUDIO.center,
                "power": 2.0,
            },
        )

        if verbose:
            print(f"[data] {split} 构建完成：{len(self):,} 条，"
                  f"其中静音 {n_silence:,} / 未知 {min(n_unknown, len(other_files)):,}")

    # ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.items)

    def label_distribution(self) -> dict[str, int]:
        counts = {label: 0 for label in LABELS}
        for _, idx in self.items:
            counts[LABELS[idx]] += 1
        return counts

    # ------------------------------------------------------------
    def _load_waveform(self, path: Path | None, label_idx: int) -> torch.Tensor:
        """读一条样本，返回长度恰为 clip_samples 的波形。"""
        target = AUDIO.clip_samples

        if path is None:                                    # 合成静音
            wav = _random_noise_slice(self.noise_store, target, self.rng)
            # 静音样本整体压低音量，避免它“听起来像关键词”
            wav = wav * self.rng.uniform(0.05, 0.35)
            return wav

        wav, sr = torchaudio.load(str(path))
        if sr != AUDIO.sample_rate:
            wav = torchaudio.functional.resample(wav, sr, AUDIO.sample_rate)
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        wav = wav.squeeze(0)

        if self.augment:
            # 随机平移：先补到 target + 2*shift，再随机开窗
            shift = int(AUDIO.sample_rate * DATA.max_shift_ms / 1000)
            total = target + 2 * shift
            wav = _pad_or_crop(wav, total)
            start = self.rng.randrange(0, 2 * shift + 1)
            wav = wav[start:start + target]
        else:
            wav = _pad_or_crop(wav, target)

        return wav

    def _augment_waveform(self, wav: torch.Tensor) -> torch.Tensor:
        if not self.augment:
            return wav

        # 背景噪声混合
        if self.rng.random() < DATA.noise_prob:
            snr = self.rng.uniform(DATA.min_snr_db, DATA.max_snr_db)
            noise = _random_noise_slice(
                self.noise_store, AUDIO.clip_samples, self.rng
            )
            wav = _mix_at_snr(wav, noise, snr)

        # 随机音量
        if self.rng.random() < DATA.gain_prob:
            wav = wav * self.rng.uniform(DATA.min_gain, DATA.max_gain)

        return wav.clamp(-1.0, 1.0)

    @staticmethod
    def _spec_augment(feat: torch.Tensor, rng: random.Random) -> torch.Tensor:
        """SpecAugment：在 (n_mfcc, n_frames) 上随机遮挡频带和时间段。

        直觉：强迫模型不要只依赖某一个频带或某一个时刻的特征，
        等价于对时频平面做 dropout。
        """
        feat = feat.clone()
        n_mfcc, n_frames = feat.shape
        for _ in range(DATA.n_freq_masks):
            f = rng.randint(0, DATA.freq_mask_param)
            if f > 0:
                f0 = rng.randint(0, max(0, n_mfcc - f))
                feat[f0:f0 + f, :] = 0.0
        for _ in range(DATA.n_time_masks):
            t = rng.randint(0, DATA.time_mask_param)
            if t > 0:
                t0 = rng.randint(0, max(0, n_frames - t))
                feat[:, t0:t0 + t] = 0.0
        return feat

    # ------------------------------------------------------------
    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        path, label_idx = self.items[index]

        wav = self._load_waveform(path, label_idx)
        wav = self._augment_waveform(wav)

        feat = self.mfcc(wav.unsqueeze(0)).squeeze(0)      # (n_mfcc, n_frames)
        if self.augment and self.rng.random() < DATA.spec_augment_prob:
            feat = self._spec_augment(feat, self.rng)

        return feat.transpose(0, 1).contiguous(), label_idx   # (n_frames, n_mfcc)


# ================================================================ 合成数据（冒烟测试用）
class SyntheticKWSDataset(Dataset):
    """纯随机生成的假数据，用于在不下载 2.3 GB 的前提下验证整条流水线。

    它学不到任何东西——准确率会停在随机水平（1/6 ≈ 17%）。
    它的唯一用途是确认：模型能前向、能反向、能导出。
    """

    def __init__(self, n_samples: int = 256, seed: int = 0):
        self.n = n_samples
        g = torch.Generator().manual_seed(seed)
        self.features = torch.randn(
            n_samples, *AUDIO.feature_shape, generator=g
        )
        self.labels = torch.randint(0, len(LABELS), (n_samples,), generator=g)

    def __len__(self) -> int:
        return self.n

    def __getitem__(self, i: int):
        return self.features[i], int(self.labels[i])


# ================================================================ DataLoader
def build_dataloaders(
    root: Path | str = DATA_DIR,
    batch_size: int | None = None,
    num_workers: int | None = None,
    download: bool = True,
    synthetic: bool = False,
    verbose: bool = True,
) -> tuple[DataLoader, DataLoader, DataLoader]:
    """构建 train / val / test 三个 DataLoader。"""
    bs = batch_size or config.TRAIN.batch_size
    nw = num_workers if num_workers is not None else config.TRAIN.num_workers

    if synthetic:
        tr = DataLoader(SyntheticKWSDataset(256, seed=0), batch_size=bs, shuffle=True)
        va = DataLoader(SyntheticKWSDataset(128, seed=1), batch_size=bs)
        te = DataLoader(SyntheticKWSDataset(128, seed=2), batch_size=bs)
        return tr, va, te

    train_set = KeywordSpottingDataset("training", root, download, verbose=verbose)
    val_set = KeywordSpottingDataset("validation", root, download, verbose=verbose)
    test_set = KeywordSpottingDataset("testing", root, download, verbose=verbose)

    pin = torch.cuda.is_available()
    train_loader = DataLoader(
        train_set, batch_size=bs, shuffle=True, num_workers=nw,
        pin_memory=pin, drop_last=False, persistent_workers=nw > 0,
    )
    val_loader = DataLoader(
        val_set, batch_size=bs, shuffle=False, num_workers=nw,
        pin_memory=pin, persistent_workers=nw > 0,
    )
    test_loader = DataLoader(
        test_set, batch_size=256, shuffle=False, num_workers=0, pin_memory=pin,
    )
    return train_loader, val_loader, test_loader


if __name__ == "__main__":
    import json

    torch.manual_seed(0)
    print("MFCC 特征提取自检\n" + "-" * 60)

    # 用一段纯随机波形检查特征形状是否符合预期
    wav = torch.randn(1, AUDIO.clip_samples) * 0.01
    mfcc = T.MFCC(
        sample_rate=AUDIO.sample_rate, n_mfcc=AUDIO.n_mfcc,
        melkwargs={"n_fft": AUDIO.n_fft, "hop_length": AUDIO.hop_length,
                   "n_mels": AUDIO.n_mels, "center": AUDIO.center},
    )(wav).squeeze(0)

    print(f"期望形状 (n_mfcc, n_frames) = ({AUDIO.n_mfcc}, {AUDIO.n_frames})")
    print(f"实际形状                    = {tuple(mfcc.shape)}")
    assert tuple(mfcc.shape) == (AUDIO.n_mfcc, AUDIO.n_frames), \
        "MFCC 形状不符！请检查 config.AudioConfig 的 n_fft / hop_length 设置"
    print("OK：特征形状正确\n")

    print("合成数据集自检\n" + "-" * 60)
    ds = SyntheticKWSDataset(64)
    x, y = ds[0]
    print(f"样本形状 {tuple(x.shape)}，标签 {y} ({LABELS[y]})")
    print(f"数据集大小 {len(ds)}")
    print("\n提示：正式数据集首次使用需下载约 2.3 GB。")
    print(json.dumps({k: v for k, v in ds.label_distribution().items()}, ensure_ascii=False)
          if hasattr(ds, "label_distribution") else "")
