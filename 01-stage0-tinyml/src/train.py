"""
训练 DS-CNN 关键词唤醒模型

用法（在 01-stage0-tinyml 目录下）：

    # 1. 冒烟测试：不下载数据集，5 秒内验证整条流水线能跑通
    .\\venv\\Scripts\\python.exe src\\train.py --smoke

    # 2. 正式训练（第一次会自动下载 2.3 GB 数据集）
    .\\venv\\Scripts\\python.exe src\\train.py

    # 3. 常用调参
    .\\venv\\Scripts\\python.exe src\\train.py --epochs 50 --batch-size 256 --lr 1e-3

产出（runs/<运行名>/）：
    best.pt        验证集准确率最高的权重
    last.pt        最后一轮权重
    config.json    本次运行的完整超参（复现用，务必保留）
    history.csv    每轮的 loss / acc / lr
    curves.png     训练曲线
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.optim import AdamW
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                    # noqa: E402
from config import AUDIO, LABELS, RUNS_DIR, TRAIN  # noqa: E402
from dataset import build_dataloaders              # noqa: E402
from model import build_model, count_parameters    # noqa: E402


# ================================================================ 可复现性
def set_seed(seed: int) -> None:
    """固定所有随机源。

    注意：完全可复现还要求 num_workers=0 且关闭 TF32 等非确定性优化。
    CPU 训练 + 小模型的情况下，这里的设置足够让多次运行的差距 <0.3%。
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# ================================================================ 学习率调度
def build_scheduler(optimizer, epochs: int, steps_per_epoch: int, cfg=TRAIN):
    """warmup（线性升温）+ cosine（余弦退火）。

    为什么需要 warmup：
        训练刚开始时 BN 统计量还没稳定，用大学习率容易把模型带跑偏。
        前几轮用小 lr 让统计量先稳定下来。
    为什么用 cosine：
        后期学习率缓慢趋零，比阶梯式下降更容易收敛到更平坦的极小值，
        而平坦极小值对量化扰动更鲁棒——这一点在这条路线上尤其重要。
    """
    total_steps = max(1, epochs * steps_per_epoch)
    warmup_steps = max(1, cfg.warmup_epochs * steps_per_epoch)

    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return float(step + 1) / float(warmup_steps)
        progress = (step - warmup_steps) / float(max(1, total_steps - warmup_steps))
        progress = min(1.0, progress)
        cosine = 0.5 * (1.0 + np.cos(np.pi * progress))
        return cfg.min_lr_ratio + (1.0 - cfg.min_lr_ratio) * cosine

    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


# ================================================================ 单轮
@torch.no_grad()
def evaluate(model: nn.Module, loader, device: torch.device, criterion=None):
    """在给定 loader 上评估，返回 (平均 loss, 平均准确率, 预测, 真值)。"""
    model.eval()
    total_loss, total_correct, total = 0.0, 0, 0
    preds_all, targets_all = [], []

    for x, y in loader:
        x, y = x.to(device), y.to(device)
        logits = model(x)
        loss = criterion(logits, y) if criterion else nn.functional.cross_entropy(logits, y)

        total_loss += loss.item() * y.size(0)
        total_correct += (logits.argmax(1) == y).sum().item()
        total += y.size(0)
        preds_all.append(logits.argmax(1).cpu())
        targets_all.append(y.cpu())

    preds = torch.cat(preds_all)
    targets = torch.cat(targets_all)
    return (
        total_loss / max(1, total),
        total_correct / max(1, total),
        preds,
        targets,
    )


def train_one_epoch(model, loader, optimizer, scheduler, criterion,
                    device, epoch, total_epochs):
    model.train()
    total_loss, total_correct, total, seen = 0.0, 0, 0, 0

    bar = tqdm(loader, desc=f"  epoch {epoch}/{total_epochs}", leave=False)
    for x, y in bar:
        x, y = x.to(device), y.to(device)
        logits = model(x)
        loss = criterion(logits, y)

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), TRAIN.grad_clip)
        optimizer.step()
        scheduler.step()

        bs = y.size(0)
        total_loss += loss.item() * bs
        total_correct += (logits.argmax(1) == y).sum().item()
        total += bs
        seen += bs

        bar.set_postfix(
            loss=f"{total_loss / seen:.4f}",
            acc=f"{total_correct / seen:.3%}",
            lr=f"{scheduler.get_last_lr()[0]:.2e}",
        )

    return total_loss / max(1, total), total_correct / max(1, total)


# ================================================================ 绘图
def plot_curves(history: list[dict], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        epochs = [h["epoch"] for h in history]
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))

        axes[0].plot(epochs, [h["train_loss"] for h in history], label="train")
        axes[0].plot(epochs, [h["val_loss"] for h in history], label="val")
        axes[0].set_title("Loss"); axes[0].set_xlabel("epoch"); axes[0].legend(); axes[0].grid(alpha=.3)

        axes[1].plot(epochs, [h["train_acc"] for h in history], label="train")
        axes[1].plot(epochs, [h["val_acc"] for h in history], label="val")
        axes[1].axhline(0.95, ls="--", c="r", lw=1, label="target 95%")
        axes[1].set_title("Accuracy"); axes[1].set_xlabel("epoch"); axes[1].legend(); axes[1].grid(alpha=.3)

        axes[2].plot(epochs, [h["lr"] for h in history], color="tab:green")
        axes[2].set_title("Learning rate"); axes[2].set_xlabel("epoch"); axes[2].grid(alpha=.3)

        fig.tight_layout()
        fig.savefig(out_path, dpi=130)
        plt.close(fig)
    except Exception as e:                                  # 绘图失败不该中断训练
        print(f"[warn] 训练曲线绘制失败（不影响训练）：{e}")


# ================================================================ 主流程
def main() -> int:
    p = argparse.ArgumentParser(description="训练 DS-CNN KWS 模型")
    p.add_argument("--run-name", default=None, help="运行名，默认用时间戳")
    p.add_argument("--epochs", type=int, default=TRAIN.epochs)
    p.add_argument("--batch-size", type=int, default=TRAIN.batch_size)
    p.add_argument("--num-workers", type=int, default=TRAIN.num_workers,
                   help="Windows 下若报多进程错误，设为 0")
    p.add_argument("--lr", type=float, default=TRAIN.lr)
    p.add_argument("--weight-decay", type=float, default=TRAIN.weight_decay)
    p.add_argument("--seed", type=int, default=TRAIN.seed)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--data-root", default=str(config.DATA_DIR))
    p.add_argument("--resume", default=None, help="从某个 best.pt 继续训练")
    p.add_argument("--smoke", action="store_true",
                   help="冒烟测试：用合成数据跑 2 轮，不下载数据集")
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args()

    set_seed(args.seed)
    device = torch.device(args.device)

    run_name = args.run_name or datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.smoke:
        run_name = f"smoke-{run_name}"
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(f"  阶段 0 · DS-CNN 关键词唤醒训练")
    print(f"  运行名 : {run_name}")
    print(f"  设备   : {device}   标签: {LABELS}")
    if args.smoke:
        print("  模式   : 【冒烟测试】使用合成随机数据，准确率无意义")
    print("=" * 68)

    # ---- 数据 ----
    epochs = 2 if args.smoke else args.epochs
    bs = 32 if args.smoke else args.batch_size
    nw = 0 if args.smoke else args.num_workers

    t0 = time.time()
    train_loader, val_loader, test_loader = build_dataloaders(
        root=args.data_root, batch_size=bs, num_workers=nw,
        download=not args.smoke, synthetic=args.smoke,
    )
    print(f"[data] 数据就绪，耗时 {time.time() - t0:.1f}s")
    if hasattr(train_loader.dataset, "label_distribution"):
        print(f"[data] 训练集分布: {train_loader.dataset.label_distribution()}")

    # ---- 模型 ----
    model = build_model().to(device)
    n_params = count_parameters(model)
    print(f"[model] 参数量 {n_params:,}  →  INT8 权重约 {n_params / 1024:.1f} KB")

    if args.resume:
        ckpt = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state"])
        print(f"[model] 已从 {args.resume} 恢复权重")

    criterion = nn.CrossEntropyLoss(label_smoothing=TRAIN.label_smoothing)
    optimizer = AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = build_scheduler(optimizer, epochs, len(train_loader))

    # ---- 保存本次配置 ----
    cfg_dump = config.summary()
    cfg_dump["run"] = {
        "name": run_name, "epochs": epochs, "batch_size": bs,
        "lr": args.lr, "seed": args.seed, "device": str(device),
        "smoke": args.smoke, "num_params": n_params,
        "torch_version": torch.__version__,
    }
    (run_dir / "config.json").write_text(
        json.dumps(cfg_dump, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ---- 训练循环 ----
    history: list[dict] = []
    best_acc, best_epoch, patience = 0.0, 0, 0

    print(f"\n[train] 开始训练 {epochs} 轮（Ctrl+C 可随时中断，已保存 best.pt）\n")
    try:
        for epoch in range(1, epochs + 1):
            tr_loss, tr_acc = train_one_epoch(
                model, train_loader, optimizer, scheduler,
                criterion, device, epoch, epochs,
            )
            va_loss, va_acc, _, _ = evaluate(model, val_loader, device, criterion)
            lr_now = scheduler.get_last_lr()[0]

            history.append({
                "epoch": epoch, "train_loss": tr_loss, "train_acc": tr_acc,
                "val_loss": va_loss, "val_acc": va_acc, "lr": lr_now,
            })

            mark = ""
            if va_acc > best_acc:
                best_acc, best_epoch, patience = va_acc, epoch, 0
                mark = "  * best"
                torch.save({
                    "epoch": epoch, "model_state": model.state_dict(),
                    "val_acc": va_acc, "config": cfg_dump,
                }, run_dir / "best.pt")
            else:
                patience += 1

            print(f"  epoch {epoch:>3}/{epochs}  "
                  f"train {tr_loss:.4f}/{tr_acc:.2%}   "
                  f"val {va_loss:.4f}/{va_acc:.2%}   "
                  f"lr {lr_now:.2e}{mark}")

            if patience >= TRAIN.early_stop_patience and not args.smoke:
                print(f"\n[train] 验证集连续 {patience} 轮未提升，提前停止。")
                break

    except KeyboardInterrupt:
        print("\n[train] 收到中断信号，保存当前进度...")

    torch.save({
        "epoch": len(history), "model_state": model.state_dict(),
        "val_acc": history[-1]["val_acc"] if history else 0.0,
        "config": cfg_dump,
    }, run_dir / "last.pt")

    # ---- 保存历史 ----
    with open(run_dir / "history.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(history[0].keys()))
        w.writeheader()
        w.writerows(history)
    if not args.no_plot and history:
        plot_curves(history, run_dir / "curves.png")

    # ---- 测试集最终评估 ----
    print("\n[eval] 用 best.pt 在测试集上评估")
    ckpt = torch.load(run_dir / "best.pt", map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state"])
    te_loss, te_acc, _, _ = evaluate(model, test_loader, device, criterion)

    summary = {
        "best_val_acc": best_acc, "best_epoch": best_epoch,
        "test_acc": te_acc, "test_loss": te_loss,
        "num_params": n_params,
        "int8_size_kb_est": round(n_params / 1024, 1),
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("=" * 68)
    print(f"  最佳验证准确率 : {best_acc:.2%}  (epoch {best_epoch})")
    print(f"  测试集准确率   : {te_acc:.2%}")
    print(f"  参数量 / INT8  : {n_params:,} / 约 {n_params / 1024:.1f} KB")
    print(f"  产物目录       : {run_dir}")
    if not args.smoke:
        print(f"\n  验收线：测试准确率 > 95%，INT8 体积 < 80 KB")
        if te_acc >= 0.95:
            print("  状态  : 通过 —— 可以进入量化环节")
        else:
            print("  状态  : 未达标 —— 见手册「准确率不达标怎么办」一节")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
