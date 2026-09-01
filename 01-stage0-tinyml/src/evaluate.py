"""
评估训练好的模型：准确率、混淆矩阵、逐类指标、误唤醒分析

用法：
    .\\venv\\Scripts\\python.exe src\\evaluate.py --ckpt runs\\<运行名>\\best.pt
    .\\venv\\Scripts\\python.exe src\\evaluate.py --ckpt runs\\<运行名>\\best.pt --save-preds

为什么单独做评估脚本，而不是只看 train.py 最后打印的那个数字？
    因为「总准确率」会掩盖问题。一个把 _unknown_ 全判成 yes 的模型，
    总准确率也可能有 70%，但它完全不能用。真正决定产品体验的是：
      - 关键词的召回率（说了没反应，用户最恼火）
      - _silence_ / _unknown_ 的误判率（误唤醒，用户第二恼火）
    混淆矩阵能一眼看出问题出在哪一类。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                   # noqa: E402
from config import AUDIO, LABELS, SILENCE_LABEL, UNKNOWN_LABEL  # noqa: E402
from dataset import build_dataloaders            # noqa: E402
from model import build_model                    # noqa: E402


def confusion_matrix(preds: np.ndarray, targets: np.ndarray, n: int) -> np.ndarray:
    """行=真值，列=预测。不依赖 sklearn，部署环境下也能跑。"""
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
            cell = f"{v}"
            if i == j:
                cell = f"[{v}]"
            line += cell.rjust(width)
        print(line)
    print("-" * len(header))


def per_class_report(cm: np.ndarray, labels: list[str]) -> list[dict]:
    rows = []
    for i, name in enumerate(labels):
        tp = int(cm[i, i])
        fn = int(cm[i, :].sum() - tp)          # 本类被判成别的
        fp = int(cm[:, i].sum() - tp)          # 别的被判成本类
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append({
            "class": name, "support": int(cm[i, :].sum()),
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "false_positives": fp, "false_negatives": fn,
        })
    return rows


@torch.no_grad()
def run_inference(model, loader, device):
    model.eval()
    probs_all, preds_all, targets_all = [], [], []
    for x, y in tqdm(loader, desc="  推理", leave=False):
        x = x.to(device)
        logits = model(x)
        probs_all.append(F.softmax(logits, dim=1).cpu())
        preds_all.append(logits.argmax(1).cpu())
        targets_all.append(y)
    return (
        torch.cat(probs_all).numpy(),
        torch.cat(preds_all).numpy(),
        torch.cat(targets_all).numpy(),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="评估 KWS 模型")
    p.add_argument("--ckpt", required=True, help="best.pt 路径")
    p.add_argument("--split", default="testing", choices=["validation", "testing"])
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--num-workers", type=int, default=0)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--data-root", default=str(config.DATA_DIR))
    p.add_argument("--out", default=None, help="把报告存成 json")
    args = p.parse_args()

    device = torch.device(args.device)
    ckpt_path = Path(args.ckpt)
    if not ckpt_path.exists():
        print(f"[错误] 找不到权重文件：{ckpt_path}")
        return 1

    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model = build_model().to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    print(f"[model] 已加载 {ckpt_path}  (训练时 val_acc={ckpt.get('val_acc', '?')})")

    _, val_loader, test_loader = build_dataloaders(
        root=args.data_root, batch_size=args.batch_size,
        num_workers=args.num_workers, download=True, verbose=False,
    )
    loader = test_loader if args.split == "testing" else val_loader

    probs, preds, targets = run_inference(model, loader, device)
    acc = float((preds == targets).mean())
    n = len(LABELS)
    cm = confusion_matrix(preds, targets, n)
    rows = per_class_report(cm, LABELS)

    print("\n" + "=" * 68)
    print(f"  数据集 : {args.split}    样本数 {len(targets):,}")
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

    # ---- 关键业务指标 ----
    sil_i = LABELS.index(SILENCE_LABEL)
    unk_i = LABELS.index(UNKNOWN_LABEL)
    kw_idx = [LABELS.index(k) for k in config.KEYWORDS]

    kw_recall = float(np.mean([cm[i, i] / cm[i, :].sum() for i in kw_idx]))
    false_accept = float((cm[sil_i, :].sum() - cm[sil_i, sil_i]
                          + cm[unk_i, :].sum() - cm[unk_i, unk_i])
                         / max(1, cm[sil_i, :].sum() + cm[unk_i, :].sum()))

    print("\n业务指标")
    print("-" * 66)
    print(f"  关键词平均召回率   : {kw_recall:.2%}   （越高越好，目标 > 95%）")
    print(f"  负样本误接受率 FA  : {false_accept:.2%}   （越低越好，目标 < 5%）")
    print("  注：FA 指把静音/未知词判成 4 个关键词之一的比例，直接决定误唤醒次数。")

    # ---- 置信度分布（后面做量化对比时要用）----
    conf = probs.max(axis=1)
    print(f"\n  预测置信度：均值 {conf.mean():.3f}  中位 {np.median(conf):.3f}  "
          f"P05 {np.percentile(conf, 5):.3f}")
    print("  提示：置信度整体偏低说明模型对量化扰动会很敏感，量化后掉点会更多。")

    report = {
        "ckpt": str(ckpt_path), "split": args.split, "accuracy": acc,
        "keyword_recall": kw_recall, "false_accept_rate": false_accept,
        "per_class": rows, "confusion_matrix": cm.tolist(),
        "confidence": {"mean": float(conf.mean()), "median": float(np.median(conf)),
                       "p05": float(np.percentile(conf, 5))},
    }
    out_path = Path(args.out) if args.out else ckpt_path.parent / f"eval_{args.split}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  报告已保存：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
