#!/usr/bin/env python
"""
F407 USB CDC 串口捕获工具

用途：烧录后抓取自检固件的串口输出，非交互式（可被自动化调用）。

用法：
    python serial_capture.py                      等新 COM 口出现，读 30 秒
    python serial_capture.py --duration 20        读 20 秒
    python serial_capture.py --port COM5          指定端口
    python serial_capture.py --send 1 --send 3    依次发命令（每个间隔 4 秒）

说明：
    F407 的 Serial 是 USB CDC（PA11/PA12），必须插板载 USB 口才出 COM 口。
    固件上电只打印一次横幅，所以开始读取后请按一下板上的 RESET 键。
"""

import argparse
import sys
import time

import serial
import serial.tools.list_ports


def list_ports():
    return {p.device for p in serial.tools.list_ports.comports()}


def wait_for_new_port(baseline, timeout=180, poll=1.0):
    print(f"[等待] 当前已有端口: {sorted(baseline) or '无'}")
    print("[等待] 请现在把 F407 的 USB 口接到电脑（最长等 180 秒）...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = list_ports()
        new = now - baseline
        if new:
            port = sorted(new)[0]
            print(f"[发现] 新端口 {port}", flush=True)
            return port
        time.sleep(poll)
    print("[超时] 没检测到新 COM 口。检查：USB 线是否支持数据（不是纯充电线）", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="指定 COM 口，不给则等待新端口出现")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--duration", type=float, default=30.0, help="读取总时长（秒）")
    ap.add_argument("--send", action="append", default=[], help="要发送的命令，可重复")
    ap.add_argument("--repeat", type=int, default=1, help="每条 --send 命令重复发送的次数")
    ap.add_argument("--no-echo", action="store_true",
                    help="不实时打印收到的数据，只在结尾给统计（长时间运行防刷屏）")
    ap.add_argument("--send-after", type=float, default=6.0, help="开读后多久开始发命令")
    ap.add_argument("--send-gap", type=float, default=5.0, help="两条命令之间的间隔")
    args = ap.parse_args()

    port = args.port
    if not port:
        port = wait_for_new_port(list_ports())
        if not port:
            return 2
        time.sleep(2.0)

    print(f"[打开] {port} @ {args.baud}", flush=True)
    try:
        ser = serial.Serial(port, args.baud, timeout=0.3)
    except Exception as e:
        print(f"[失败] 打不开 {port}: {e}")
        return 3

    # STM32 USB CDC 靠 DTR 判断主机是否就绪：拉低会导致固件不发数据。
    # 必须先拉高 DTR，再给固件一点时间完成 USB 重枚举。
    try:
        ser.setDTR(True)
        ser.setRTS(False)
        time.sleep(0.3)
        ser.setDTR(True)
    except Exception as e:
        print(f"[警告] 设置 DTR/RTS 失败: {e}", flush=True)
    time.sleep(1.0)

    print("=" * 60)
    print("[提示] 现在请按一下 F407 板上的 RESET 键，重看完整启动横幅")
    print("=" * 60, flush=True)

    buf = bytearray()
    start = time.time()
    sent = 0
    next_send = start + args.send_after
    commands = []
    for c in args.send:
        commands.extend([c] * args.repeat)
    if commands:
        print(f"[计划] 共发 {len(commands)} 条命令：{commands}", flush=True)

    try:
        while time.time() - start < args.duration:
            if sent < len(commands) and time.time() >= next_send:
                cmd = commands[sent]
                ser.write((cmd + "\n").encode())
                print(f"\n[发送] {cmd}  ({sent + 1}/{len(commands)})", flush=True)
                sent += 1
                next_send = time.time() + args.send_gap
            try:
                chunk = ser.read(4096)
            except serial.SerialException:
                print("\n[重连] 串口断开（多半是刚按了 RESET，USB 重枚举）...", flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                t0 = time.time()
                while time.time() - t0 < 20:
                    try:
                        ser = serial.Serial(port, args.baud, timeout=0.3)
                        break
                    except Exception:
                        time.sleep(0.5)
                if ser is None:
                    print("[失败] 20 秒内没能重连上端口", flush=True)
                    break
                try:
                    ser.setDTR(True)
                    ser.setRTS(False)
                except Exception:
                    pass
                print("[重连] 成功，继续监听", flush=True)
                continue
            if chunk:
                buf.extend(chunk)
                if not args.no_echo:
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
            else:
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    print("\n" + "=" * 60)
    print(f"[结束] 共收到 {len(buf)} 字节，耗时 {time.time() - start:.1f} 秒，发出 {sent} 条命令")
    if not buf:
        print("[空] 没收到任何数据。可能原因：")
        print("     1. USB 线是纯充电线，无数据线")
        print("     2. F407 USB 口没插 / 没供电")
        print("     3. 波特率不对（固件用 115200）")
        print("     4. 没按 RESET，横幅早在打开串口前就打印完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
