#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
感知端单元测试 —— 不需要摄像头、不需要板子
==========================================
用合成图像验证 vision_tracker.py 的检测与瞄准逻辑，硬件到货前先把"眼睛"测好。

    python test_detection.py
    （全过 → exit 0；有挂 → exit 1 并打印哪条挂了）
"""

import sys

import cv2
import numpy as np

from vision_tracker import find_color, update_aim

W, H = 640, 480
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def frame_with_ball(cx, cy, r):
    """黑底 + 实心绿色圆（BGR 绿 = (0,255,0)），模拟摄像头画面。"""
    f = np.zeros((H, W, 3), dtype=np.uint8)
    cv2.circle(f, (cx, cy), r, (0, 255, 0), -1)
    return f


print("== 1. 目标检测 find_color ==")

# 1a. 右侧有球 → 找到，中心 x 接近真实值（容差 ±6px）
f = frame_with_ball(480, 240, 40)
cx, box = find_color(f)
check("右侧绿球被检测到", cx is not None)
if cx is not None:
    check("中心 x 精度", abs(cx - 480) <= 6, f"cx={cx}, 期望≈480")

# 1b. 左侧有球
f = frame_with_ball(120, 300, 50)
cx, _ = find_color(f)
check("左侧绿球被检测到且位置对", cx is not None and abs(cx - 120) <= 6,
      f"cx={cx}")

# 1c. 空画面 → None（不误检）
cx, _ = find_color(np.zeros((H, W, 3), dtype=np.uint8))
check("空画面返回 None", cx is None)

# 1d. 太小的球（r=5，面积约 78 < 面积阈值300）→ 当噪声忽略
cx, _ = find_color(frame_with_ball(320, 240, 5))
check("过小目标被过滤", cx is None)

# 1e. 红色球（不该被绿色阈值抓到）
f = np.zeros((H, W, 3), dtype=np.uint8)
cv2.circle(f, (320, 240), 60, (0, 0, 255), -1)
cx, _ = find_color(f)
check("红色目标不被误检", cx is None)

print("== 2. 增量式 PID update_aim ==")

# 2a. 误差 +0.5（目标偏右），prev=0 → delta = Kp·err + Kd·Δerr = 0.35·0.5 + 0.05·0.5 = 0.2
aim, pe = update_aim(0.0, 0.5, 0.0, kp=0.35, kd=0.05, deadband=0.03, limit=2.0)
check("误差0.5 → aim≈+0.2（含D项）", abs(aim - 0.2) < 1e-6, f"aim={aim:.4f}")
check("返回新 prev_err = err", pe == 0.5)

# 2b. 死区：误差 0.02 < 0.03 → aim 不变；prev_err 仍跟随 err（出死区时 D 项不突跳）
aim2, pe2 = update_aim(0.175, 0.02, 0.5, kp=0.35, kd=0.05, deadband=0.03, limit=2.0)
check("死区内 aim 不动", aim2 == 0.175, f"aim={aim2}")
check("死区内 prev_err 跟随 err", pe2 == 0.02, f"prev={pe2}")

# 2c. 阻尼：同方向误差减小（err-prev<0）→ 增量比纯 P 小
aim3, _ = update_aim(0.0, 0.3, 0.5, kp=0.35, kd=0.05, deadband=0.03, limit=2.0)
check("Kd 阻尼使增量 < 纯P", aim3 < 0.35 * 0.3, f"aim={aim3:.4f} vs 纯P={0.35*0.3:.4f}")

# 2d. 限位：持续大误差 → aim 夹在 ±2.0
aim4 = 0.0
for _ in range(100):
    aim4, _ = update_aim(aim4, 1.0, 1.0, kp=0.35, kd=0.0, deadband=0.0, limit=2.0)
check("限位夹在 ±2.0 rad", abs(aim4 - 2.0) < 1e-6, f"aim={aim4}")

print(f"\n结果：{len(PASS)} 过 / {len(FAIL)} 挂")
if FAIL:
    print("挂掉的：", FAIL)
    sys.exit(1)
print("感知端逻辑全部通过 ✓  （人脸检测依赖真实图像，装好摄像头后用 --no-serial 实测）")
