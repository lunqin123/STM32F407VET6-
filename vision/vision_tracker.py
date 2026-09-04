#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云台视觉追踪 —— PC 端（感知+决策）
==================================
摄像头检测目标 → 像素误差 → 增量式 PID 更新"瞄准角" → 串口发 T<弧度> 给 F407。

【用法】
    pip install opencv-python pyserial
    python vision_tracker.py --port COM5 --source color      # 追绿色物体
    python vision_tracker.py --port COM5 --source face       # 追人脸
    python vision_tracker.py --port COM5 --kp 0.5 --kd 0.05  # 调追踪手感
    python vision_tracker.py --no-serial --source color      # 没板子也能跑：
                                                             # 只看检测和瞄准角，
                                                             # 验证感知端

【为什么要"增量 PID"而不是直接像素→角度换算】
    直接换算需要相机焦距标定，麻烦。增量式（每帧 aim += Kp*err）不需要任何
    标定：目标偏左 → 角度慢慢往左加 → 直到目标回到画面中心。这就是
    visual servoing 最经典的"无标定视觉伺服"思路，先跑通再谈精调。

【协议】一行一条命令，115200：
    T<弧度>\n   设定目标角（F407 角度环执行）
    M2\n        确保角度模式（固件默认就是，保险起见发一次）

【快捷键】q 退出 | f 切换 color/face 检测
"""

import argparse
import time

import cv2
import serial


# ---------- 目标检测 ----------
def find_color(frame, debug=True):
    """找最大的绿色物体，返回中心 x（像素）或 None。换颜色改 lower/upper。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = (35, 80, 80)    # 绿色 H 范围 35~85；追别的颜色改这里
    upper = (85, 255, 255)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, (5, 5))   # 去噪点
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 300:                            # 太小的当噪声
        return None, None
    x, y, w, h = cv2.boundingRect(c)
    cx = x + w // 2
    box = (x, y, w, h)
    return cx, box


_face_cascade = None
def find_face(frame):
    """Haar 级联找人脸，返回最大脸的中心 x 或 None（无需下载模型，cv2 自带）。"""
    global _face_cascade
    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = _face_cascade.detectMultiScale(gray, 1.15, 5)
    if len(faces) == 0:
        return None, None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return x + w // 2, (x, y, w, h)


# ---------- 瞄准角更新（纯函数，方便单测 test_detection.py） ----------
def update_aim(aim, err, prev_err, kp, kd, deadband, limit):
    """增量式 PID：误差归一化后更新瞄准角。返回 (新aim, 新prev_err)。
    - 死区内不动（防中心抖动）
    - Kd 提供阻尼，抑制过冲
    - aim 限位在 ±limit（安全，防止线缆缠绕/超程）"""
    if abs(err) <= deadband:
        return aim, err
    delta = kp * err + kd * (err - prev_err)
    aim = max(-limit, min(limit, aim + delta))
    return aim, err


# ---------- 主循环 ----------
def main():
    ap = argparse.ArgumentParser(description="云台视觉追踪 PC 端")
    ap.add_argument("--port", required=True, help="F407 虚拟串口，如 COM5")
    ap.add_argument("--cam", type=int, default=0, help="摄像头序号，默认 0")
    ap.add_argument("--source", choices=["color", "face"], default="color")
    ap.add_argument("--kp", type=float, default=0.35,
                    help="比例增益：误差归一化后每帧加的角度(rad)")
    ap.add_argument("--kd", type=float, default=0.05, help="微分增益（阻尼，防过冲）")
    ap.add_argument("--deadband", type=float, default=0.03,
                    help="死区（误差小于画面宽度的 3% 不动，防抖）")
    ap.add_argument("--limit", type=float, default=2.0,
                    help="瞄准角安全限位 ±limit (rad)")
    ap.add_argument("--no-serial", action="store_true",
                    help="干跑：不连板子，只跑视觉（没硬件时验证感知端）")
    args = ap.parse_args()

    ser = None
    if not args.no_serial:
        ser = serial.Serial(args.port, 115200, timeout=0.1)
        time.sleep(2.0)                   # 等 USB CDC 枚举 + F407 启动
        ser.write(b"M2\n")                # 保险：确保角度模式
        print(f"已连接 {args.port}，检测源 = {args.source}")
    else:
        print("【干跑模式】不连串口，只验证视觉。检测源 =", args.source)

    cam = cv2.VideoCapture(args.cam)
    if not cam.isOpened():
        raise SystemExit(f"摄像头 {args.cam} 打不开")
    # 降低分辨率换帧率：追踪不需要 1080p
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    aim = 0.0                             # 当前瞄准角（rad，0 = 上电零点）
    prev_err = 0.0
    source = args.source
    last_send = 0.0

    while True:
        ok, frame = cam.read()
        if not ok:
            break
        H, W = frame.shape[:2]
        center = W // 2

        cx, box = (find_color(frame) if source == "color" else find_face(frame))

        if cx is not None:
            err = (cx - center) / (W / 2)          # 误差归一化到 [-1, 1]
            aim, prev_err = update_aim(aim, err, prev_err,
                                       args.kp, args.kd,
                                       args.deadband, args.limit)
        # 目标丢失：保持当前角度（别乱甩；进阶可加"丢失回中"策略）

        # 串口发目标角，30fps 一帧一条足够（跟摄像头帧率天然同步）
        now = time.time()
        if ser is not None and now - last_send > 0.03:
            ser.write(f"T{aim:.3f}\n".encode())
            last_send = now

        # ---------- 画调试界面 ----------
        cv2.line(frame, (center, 0), (center, H), (200, 200, 200), 1)
        if box is not None:
            x, y, w, h = box
            color = (0, 255, 0) if source == "color" else (255, 200, 0)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.circle(frame, (cx, y + h // 2), 4, (0, 0, 255), -1)
        cv2.putText(frame, f"aim={aim:+.2f} rad  src={source}",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow("gimbal tracker (q quit, f switch source)", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("f"):
            source = "face" if source == "color" else "color"
            prev_err = 0.0
            print("切换检测源 ->", source)

    cam.release()
    if ser is not None:
        ser.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
