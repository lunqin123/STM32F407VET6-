#!/usr/bin/env python
"""
F407 闭环调试台 —— 自动跑测试序列、采遥测、算指标、出图

【为什么需要它】
手工"发命令 → 看波形 → 肉眼估超调"有三个硬伤：
  ① 不可复现（每次手感不同）  ② 无可比数字（没法判断改参数是变好还是变坏）
  ③ 无法归档（面试要的是数字，不是"感觉更稳了"）
本工具把"一次测试"固化成一条流水线：
    发命令序列 → 采遥测 → 算指标 → 存 CSV + SVG → 打印结论

【依赖】仅 pyserial + Python 标准库。不依赖 numpy / matplotlib（图由本脚本自绘 SVG）。

【前置】固件需支持 O 命令（见 src/closedloop.cpp 的"3b. 遥测"段）。
        遥测默认关闭，本工具自动开启，**结束/中断时自动关闭**（不留负担给控制环）。

【用法】
  # 位置阶跃（最常用；L1 验收的主力实验）
  python serial_bench.py step --target-deg 90 --port COM5

  # 带参数覆盖（整定矩阵就是这样脚本化的，见《调试台与整定矩阵.md》）
  python serial_bench.py step --target-deg 90 --limit 3 --set F0.05 --set A15

  # 速度阶跃
  python serial_bench.py velstep --speed 3.0 --port COM5

  # 恒速稳定性：看稳态波动与量化噪声（10 秒）
  python serial_bench.py hold --speed 3.0 --record 10

  # 可复现性：同一实验重复 10 次，看离散度（L1 验收要求"同命令 10 次一致"）
  python serial_bench.py repro --test step --target-deg 90 --repeat 10

  # 无硬件自检：验证"指标算法"本身正确（二阶系统理论超调 16.30%）
  python serial_bench.py selftest

  # 原始抓取（不跑序列，只采数）
  python serial_bench.py raw --duration 10

  # 硬件体检：不用 12V，手转电机轴，判断编码器有没有在读数
  # （角度恒 0 / 怀疑编码器坏时，第一步先跑这个）
  python serial_bench.py check --port COM5

【输出】tools/runs/<时间戳>_<测试>.csv 与 .svg，终端同时打印指标表。

【关键设计说明】
  ① 步进起始时刻由**遥测里的 target 字段变化**判定（设备侧），不是主机发送时间戳
     —— 因此不受 USB 传输延迟影响，时间轴精度 = 遥测周期。
  ② 位置阶跃以**当前位置**为起点做相对阶跃，不用绝对角 0 —— 电机的累计角会随
     实验不断累积（跑过 hold 之后可能已在第 N 圈），用绝对 0 会让电机倒转几十圈、
     --settle 来不及，采到的"基线"其实在狂奔，会被误判成失控/极限环（已实测踩过）。

【退出时一定会做的事（安全设计）】
  ① 把增益恢复到已验证的稳定点 SAFE_PARK（P0.5 / I10 / F0.03）
  ② 发送 O0 关闭遥测
  理由：本工具用 --set 改的是固件 **RAM** 里的增益，退出后依然生效。曾因把失稳
  增益（P=2.0）留在固件里，导致电机在会话结束后持续剧烈振荡，只能靠复位救回。
  → 想保留自定义增益，请实验后自行重新下发，或直接写进固件默认值。
"""

import argparse
import math
import os
import statistics
import sys
import time
from collections import namedtuple

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("缺少 pyserial。安装：<managed-python> -m pip install pyserial")
    sys.exit(1)


# raw = 编码器原始累计角（固件遥测第 7 段；旧版固件没有该字段 → None）
Sample = namedtuple("Sample", "t_ms mode target angle vel raw", defaults=(None,))

MODE_NAME = {0: "torque", 1: "velocity", 2: "angle"}
RAD2DEG = 180.0 / math.pi
DEFAULT_OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")

# ★★ 退出时恢复的「安全增益」（2026-09-12 实测整定的稳定点：
#    90° 阶跃 超调 0.0% / 调节 1194 ms / 稳态误差 +0.066° / 纹波 0.26°）。
#    【为什么必须有这个】本工具的实验会通过 --set 改固件 RAM 里的增益，
#    而**退出时不会自动还原**。实测踩过一次严重事故：最后一轮把 P 调到 2.0
#    （失稳点）后退出了，增益留在固件里，电机随后持续剧烈振荡，
#    只能靠远程复位救回。此后：**任何会话结束都必须把电机留在稳定状态。**
SAFE_PARK = ["P0.5", "I10", "F0.03"]


# ============================================================
# 串口
# ============================================================
def list_ports():
    return {p.device for p in serial.tools.list_ports.comports()}


def wait_for_new_port(baseline, timeout=180, poll=1.0):
    print(f"[等待] 当前端口: {sorted(baseline) or '无'}")
    print("[等待] 请把 F407 的 USB 口接到电脑（最长 180 秒）...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = list_ports() - baseline
        if new:
            port = sorted(new)[0]
            print(f"[发现] 新端口 {port}", flush=True)
            return port
        time.sleep(poll)
    print("[超时] 未检测到新 COM 口。检查 USB 线是否支持数据（非纯充电线）", flush=True)
    return None


class Bench:
    """一次串口会话：发命令 + 采遥测。"""

    def __init__(self, ser, verbose=False, quiet=False):
        self.ser = ser
        self.samples = []
        self.rx = bytearray()
        self.logs = []          # 固件回显的非遥测行（横幅/警告/错误）
        self.bad_lines = 0
        self.verbose = verbose
        self.quiet = quiet

    # ---------- 收 ----------
    def _handle_line(self, line):
        if not line:
            return
        if line.startswith("D,"):
            parts = line.split(",")
            if len(parts) not in (6, 7):        # 6=旧固件；7=含 raw 的新固件
                self.bad_lines += 1
                return
            try:
                raw = float(parts[6]) if len(parts) >= 7 else None
                self.samples.append(Sample(int(parts[1]), int(parts[2]),
                                           float(parts[3]), float(parts[4]),
                                           float(parts[5]), raw))
            except ValueError:
                self.bad_lines += 1
            return
        self.logs.append(line)
        if self.verbose or not self.quiet:
            print(f"  [dev] {line}")

    def pump(self, seconds):
        """读 seconds 秒，解析遥测。返回本次新增样本数。"""
        n0 = len(self.samples)
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.ser.read(4096)
            except serial.SerialException as e:
                print(f"\n[警告] 串口读取异常: {e}")
                break
            if chunk:
                self.rx.extend(chunk)
                while b"\n" in self.rx:
                    raw, _, rest = self.rx.partition(b"\n")
                    self.rx = bytearray(rest)
                    self._handle_line(raw.decode("utf-8", errors="replace").strip())
            else:
                time.sleep(0.004)
        return len(self.samples) - n0

    # ---------- 发 ----------
    def send(self, cmd, settle=0.0):
        self.ser.write((cmd + "\n").encode())
        if self.verbose:
            print(f"  [>>] {cmd}")
        if settle:
            self.pump(settle)

    def clear(self):
        self.samples.clear()

    # ---------- 便捷 ----------
    def sample_rate(self):
        if len(self.samples) < 2:
            return 0.0
        span = (self.samples[-1].t_ms - self.samples[0].t_ms) / 1000.0
        return (len(self.samples) - 1) / span if span > 0 else 0.0


def open_serial(port, baud):
    try:
        ser = serial.Serial(port, baud, timeout=0.05)
    except Exception as e:
        print(f"[失败] 打不开 {port}: {e}")
        return None
    # STM32 USB CDC 靠 DTR 判断主机就绪：DTR 拉低固件不发数据。
    try:
        ser.setDTR(True)
        ser.setRTS(False)
        time.sleep(0.3)
        ser.setDTR(True)
    except Exception as e:
        print(f"[警告] 设置 DTR/RTS 失败: {e}")
    time.sleep(1.0)
    return ser


def arm_telemetry(bench, tel_ms, mode=None, limit=None):
    """开启遥测并确认真的有数据回来 —— 没有就给出排查清单。"""
    if mode is not None:
        bench.send(f"M{mode}", settle=0.2)
    if limit is not None:
        bench.send(f"L{limit}", settle=0.2)
    bench.send(f"O{tel_ms}")
    bench.pump(1.8)
    if not bench.samples:
        print("\n[失败] 已发送 O 命令但没收到遥测数据。排查清单：")
        print("  1. 固件是否为含 O 命令的版本？旧的 closedloop 固件没有遥测 —— 需要重新烧录")
        print("  2. 是否按过 RESET？USB CDC 在固件启动后才枚举")
        print("  3. USB 线是否支持数据（纯充电线不行）？")
        print("  4. 串口是否被其他程序占用（Arduino 串口监视器 / 上一个未退出的脚本）？")
        print("  5. 板子是否真的在跑（PC13 LED 应闪烁）？")
        return False
    return True


# ============================================================
# 指标计算（纯标准库）
# ============================================================
def _mean(xs):
    return statistics.fmean(xs) if xs else 0.0


def _rms(xs):
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def find_step_index(samples):
    """设备侧判定阶跃时刻：target 首次偏离基线值。返回 (idx, 基线target)。"""
    if len(samples) < 3:
        return None, None
    base = samples[0].target
    for i, s in enumerate(samples):
        if abs(s.target - base) > 1e-6:
            return i, base
    return None, base


def analyze_step(samples, field="angle", band=0.02):
    """
    阶跃响应指标。field: 'angle' 或 'vel'。
    返回 dict；样本不足时返回 {'ok': False, 'reason': ...}。

    指标定义（归一化后计算，正负阶跃通用）：
      上升时间 10%→90%（首次穿越）
      超调     max(yn) - 1，取正
      调节时间 最后一次 |yn-1| > band 的时刻（2% 带）
      稳态误差 目标 - 末段均值
      稳态纹波 末段峰峰值
    """
    idx, base = find_step_index(samples)
    if idx is None:
        return {"ok": False, "reason": "未检测到阶跃（target 字段没有变化）"}
    pre, post = samples[:idx], samples[idx:]
    if len(pre) < 5:
        return {"ok": False, "reason": f"阶跃前基线样本太少（{len(pre)}），请加大 --pre"}
    if len(post) < 20:
        return {"ok": False, "reason": f"阶跃后样本太少（{len(post)}），请加大 --record 或减小 --tel-ms"}

    y0 = _mean([getattr(s, field) for s in pre])
    y_tgt = post[0].target
    amp = y_tgt - y0
    if abs(amp) < 1e-9:
        return {"ok": False, "reason": "阶跃幅值接近 0"}

    t_step = post[0].t_ms
    ts = [(s.t_ms - t_step) / 1000.0 for s in post]
    yn = [(getattr(s, field) - y0) / amp for s in post]

    # 上升时间
    t10 = next((t for t, v in zip(ts, yn) if v >= 0.10), None)
    t90 = next((t for t, v in zip(ts, yn) if v >= 0.90), None)
    rise = (t90 - t10) if (t10 is not None and t90 is not None) else None

    # 超调
    overshoot = max(0.0, (max(yn) - 1.0) * 100.0)

    # 调节时间
    settle = None
    for t, v in reversed(list(zip(ts, yn))):
        if abs(v - 1.0) > band:
            settle = t
            break
    if settle is None:
        settle = 0.0

    # 稳态段（末 40%）
    tail_n = max(10, int(len(post) * 0.4))
    tail = post[-tail_n:]
    tail_y = [getattr(s, field) for s in tail]
    steady_mean = _mean(tail_y)
    ripple_pp = max(tail_y) - min(tail_y)
    ripple_rms = _rms([v - steady_mean for v in tail_y])
    ss_err = y_tgt - steady_mean
    peak = y0 + max(yn) * amp

    return {
        "ok": True, "field": field, "band": band,
        "y0": y0, "y_target": y_tgt, "amp": amp, "peak": peak,
        "rise_s": rise, "overshoot_pct": overshoot, "settle_s": settle,
        "steady_mean": steady_mean, "ss_err": ss_err,
        "ripple_pp": ripple_pp, "ripple_rms": ripple_rms,
        "n_pre": len(pre), "n_post": len(post), "t_step_ms": t_step,
    }


def analyze_hold(samples, field="vel"):
    """恒速/恒位保持：只统计稳态质量，不做阶跃分析。"""
    if len(samples) < 20:
        return {"ok": False, "reason": "样本太少"}
    tail_n = max(10, int(len(samples) * 0.5))
    tail = samples[-tail_n:]
    ys = [getattr(s, field) for s in tail]
    m = _mean(ys)
    return {
        "ok": True, "field": field, "target": samples[-1].target,
        "mean": m, "pp": max(ys) - min(ys),
        "rms": _rms([v - m for v in ys]),
        "ss_err": samples[-1].target - m,
        "n": len(tail), "secs": (tail[-1].t_ms - tail[0].t_ms) / 1000.0,
    }


# ============================================================
# 输出：CSV / SVG / 终端报表
# ============================================================
def write_csv(path, samples, meta):
    with open(path, "w", encoding="utf-8", newline="") as f:
        for k, v in meta.items():
            f.write(f"# {k}={v}\n")
        f.write("t_ms,mode,target_rad,angle_rad,velocity_rad_s,angle_deg,raw_rad\n")
        for s in samples:
            raw = "" if s.raw is None else f"{s.raw:.6f}"
            f.write(f"{s.t_ms},{s.mode},{s.target:.6f},{s.angle:.6f},{s.vel:.6f},"
                    f"{s.angle * RAD2DEG:.4f},{raw}\n")


def _poly(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def write_svg(path, samples, title, step_idx=None):
    """自绘 SVG（零依赖）：上=角度，下=速度。步进时刻画竖虚线。"""
    if len(samples) < 2:
        return False
    W, H = 960, 580
    PL, PR = 74, 24
    panels = [(64, 268, "angle_deg"), (334, 528, "vel")]
    t0 = samples[0].t_ms / 1000.0
    t1 = samples[-1].t_ms / 1000.0
    span_t = (t1 - t0) or 1.0
    x0, x1 = PL, W - PR

    def sx(t):
        return x0 + (t - t0) / span_t * (x1 - x0)

    def mk_scale(top, bot, vals):
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1.0, hi + 1.0
        m = (hi - lo) * 0.08
        lo, hi = lo - m, hi + m

        def sy(v):
            return bot - (v - lo) / (hi - lo) * (bot - top)

        return sy, lo, hi

    ang_deg = [s.angle * RAD2DEG for s in samples]
    sy_a, lo_a, hi_a = mk_scale(panels[0][0], panels[0][1], ang_deg)
    sy_v, lo_v, hi_v = mk_scale(panels[1][0], panels[1][1], [s.vel for s in samples])
    final_mode = samples[-1].mode

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{PL}" y="30" font-family="sans-serif" font-size="16" fill="#24292f">{title}</text>',
        f'<text x="{PL}" y="48" font-family="sans-serif" font-size="12" fill="#57606a">'
        f'{len(samples)} 样本 / {span_t:.2f} s / {len(samples) / span_t:.1f} Hz ｜ 模式 {MODE_NAME.get(final_mode, final_mode)}</text>',
    ]

    for (top, bot, label), sy, lo, hi in ((panels[0], sy_a, lo_a, hi_a), (panels[1], sy_v, lo_v, hi_v)):
        out.append(f'<rect x="{x0}" y="{top}" width="{x1 - x0}" height="{bot - top}" fill="none" stroke="#d0d7de" stroke-width="1"/>')
        for i in range(5):
            v = lo + (hi - lo) * i / 4
            y = sy(v)
            out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="#eaeef2" stroke-width="1"/>')
            out.append(f'<text x="{x0 - 8}" y="{y + 4:.1f}" font-family="sans-serif" font-size="11" fill="#57606a" text-anchor="end">{v:.3g}</text>')
        for i in range(6):
            t = t0 + span_t * i / 5
            x = sx(t)
            out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bot}" stroke="#f6f8fa" stroke-width="1"/>')
            out.append(f'<text x="{x:.1f}" y="{bot + 18}" font-family="sans-serif" font-size="11" fill="#57606a" text-anchor="middle">{t - t0:.2f}</text>')
        out.append(f'<text x="{x0 - 8}" y="{top - 8}" font-family="sans-serif" font-size="12" fill="#24292f" text-anchor="end">{label}</text>')

    if step_idx is not None and 0 <= step_idx < len(samples):
        x = sx(samples[step_idx].t_ms / 1000.0)
        out.append(f'<line x1="{x:.1f}" y1="{panels[0][0]}" x2="{x:.1f}" y2="{panels[1][1]}" stroke="#bf8700" stroke-width="1.5" stroke-dasharray="5,4"/>')
        out.append(f'<text x="{x + 6:.1f}" y="{panels[0][0] + 14}" font-family="sans-serif" font-size="11" fill="#bf8700">step</text>')

    if final_mode == 2:
        ty = sy_a(samples[-1].target * RAD2DEG)
        out.append(f'<line x1="{x0}" y1="{ty:.1f}" x2="{x1}" y2="{ty:.1f}" stroke="#1a7f37" stroke-width="1.5" stroke-dasharray="6,4"/>')
        out.append(f'<text x="{x1 - 6}" y="{ty - 6:.1f}" font-family="sans-serif" font-size="11" fill="#1a7f37" text-anchor="end">target</text>')
    elif final_mode == 1:
        ty = sy_v(samples[-1].target)
        out.append(f'<line x1="{x0}" y1="{ty:.1f}" x2="{x1}" y2="{ty:.1f}" stroke="#1a7f37" stroke-width="1.5" stroke-dasharray="6,4"/>')
        out.append(f'<text x="{x1 - 6}" y="{ty - 6:.1f}" font-family="sans-serif" font-size="11" fill="#1a7f37" text-anchor="end">target</text>')

    out.append(f'<polyline fill="none" stroke="#1f6feb" stroke-width="1.6" points="{_poly([(sx(s.t_ms / 1000.0), sy_a(s.angle * RAD2DEG)) for s in samples])}"/>')
    out.append(f'<polyline fill="none" stroke="#8250df" stroke-width="1.6" points="{_poly([(sx(s.t_ms / 1000.0), sy_v(s.vel)) for s in samples])}"/>')

    out.append(f'<text x="{PL}" y="{H - 12}" font-family="sans-serif" font-size="11" fill="#57606a">'
               f'蓝=实测角度(deg)  紫=实测速度(rad/s)  绿虚线=目标  橙虚线=阶跃时刻（由设备侧 target 字段判定）</text>')
    out.append("</svg>")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return True


def report_step(name, m):
    if not m.get("ok"):
        print(f"  ✗ {name} 分析失败：{m.get('reason')}")
        return
    u = "°" if m["field"] == "angle" else " rad"
    k = RAD2DEG if m["field"] == "angle" else 1.0
    print(f"  ── {name} ──")
    print(f"  基线 → 目标      : {m['y0'] * k:.4f}{u} → {m['y_target'] * k:.4f}{u}")
    print(f"  上升时间 (10-90%): {m['rise_s'] * 1000:.0f} ms" if m["rise_s"] is not None else "  上升时间        : 未达 90%")
    print(f"  超调            : {m['overshoot_pct']:.1f} %   (峰值 {m['peak'] * k:.4f}{u})")
    print(f"  调节时间 (±{m['band'] * 100:.0f}%) : {m['settle_s'] * 1000:.0f} ms")
    print(f"  稳态误差        : {m['ss_err'] * k:+.4f}{u}")
    print(f"  稳态纹波(峰峰)  : {m['ripple_pp'] * k:.4f}{u}   (RMS {m['ripple_rms'] * k:.4f})")
    print(f"  样本            : 基线 {m['n_pre']} + 阶跃后 {m['n_post']}")


def report_hold(name, m):
    if not m.get("ok"):
        print(f"  ✗ {name} 分析失败：{m.get('reason')}")
        return
    u = "°" if m["field"] == "angle" else " rad/s"
    print(f"  ── {name} ──")
    print(f"  目标 / 均值      : {m['target']:.4f}{u} / {m['mean']:.4f}{u}")
    print(f"  稳态误差        : {m['ss_err']:+.4f}{u}")
    print(f"  纹波 峰峰 / RMS : {m['pp']:.4f}{u} / {m['rms']:.4f}{u}")
    print(f"  统计窗口        : {m['n']} 样本 / {m['secs']:.2f} s")


# ============================================================
# 测试序列
# ============================================================
def current_angle(bench):
    """读遥测里最新的 shaft_angle（读的是电机对象的缓存成员，零副作用）。"""
    bench.pump(0.3)
    return bench.samples[-1].angle if bench.samples else 0.0


def run_step_test(bench, args, step_deg, tag="step"):
    """位置阶跃：先守住【当前位置】→ 采基线 → 相对阶跃 → 采满。

    ★ 为什么起点必须是"当前位置"，而不是绝对角 0（已实测踩过这个坑）：
      电机轴的**累计角**会随实验不断累积 —— 跑过 hold 之后可能已在第 26 圈。
      此时若有绝对 `T0`，电机会一路倒转几十圈，而 --settle 完全来不及，
      于是"基线段"采到的是电机在狂奔，会被误读成**失控 / 极限环**。
      改成"相对当前位置做阶跃"后，无论之前跑过什么都不会再踩这个坑。
    """
    cur = current_angle(bench)
    bench.send(f"T{cur:.4f}")                                # 守住当前位置（不动）
    bench.pump(args.settle)
    bench.clear()
    bench.pump(args.pre)
    bench.send(f"T{cur + math.radians(step_deg):.4f}")        # 相对阶跃
    bench.pump(args.record)
    idx, _ = find_step_index(bench.samples)
    return bench.samples, idx


def run_velstep_test(bench, args, speed):
    bench.send("T0.0000")
    bench.pump(args.settle)
    bench.clear()
    bench.pump(args.pre)
    bench.send(f"T{speed:.4f}")
    bench.pump(args.record)
    idx, _ = find_step_index(bench.samples)
    return bench.samples, idx


def run_hold_test(bench, args, speed):
    bench.send(f"T{speed:.4f}")
    bench.pump(args.settle)
    bench.clear()
    bench.pump(args.record)
    return bench.samples, None


def out_paths(args, name):
    os.makedirs(args.outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(args.outdir, f"{stamp}_{name}")
    return base + ".csv", base + ".svg"


def save(bench, args, name, title, step_idx):
    csv, svg = out_paths(args, name)
    meta = {"test": name, "port": args.port, "tel_ms": args.tel_ms,
            "voltage_limit": args.limit if args.limit is not None else "default",
            "extra_set": ";".join(getattr(args, "set", None) or []) or "-",
            "sample_rate_hz": f"{bench.sample_rate():.2f}",
            "bad_lines": bench.bad_lines}
    write_csv(csv, bench.samples, meta)
    print(f"  CSV: {csv}")
    if not args.no_svg and write_svg(svg, bench.samples, title, step_idx):
        print(f"  SVG: {svg}")
    return csv, svg


# ============================================================
# 子命令
# ============================================================
def raw_span(samples):
    """返回 (带 raw 的样本数, 跨度rad)；无 raw 字段时跨度为 None。"""
    rs = [s.raw for s in samples if s.raw is not None]
    return len(rs), (max(rs) - min(rs)) if rs else None


def cmd_raw(bench, args):
    # 先切位置模式再下发角度：否则若固件停在速度模式，T<角度> 会被当成"目标转速"
    # （角度累计值可能很大）→ 电机瞬间狂转。先 M2 + 守住当前位置最安全。
    bench.send("M2")
    bench.send(f"T{current_angle(bench):.4f}", settle=0.5)
    bench.clear()
    bench.pump(args.duration)
    print(f"\n[原始抓取] {len(bench.samples)} 样本，{bench.sample_rate():.1f} Hz")
    n_raw, span = raw_span(bench.samples)
    if span is not None:
        print(f"  raw(编码器原始角) {n_raw} 条，跨度 {span * RAD2DEG:.1f}°")
    save(bench, args, "raw", "raw capture", None)


def cmd_check(bench, args):
    """硬件体检：把"编码器坏"和"没通电/没对齐"彻底分开。

    ★ 这一步**不需要 12V**：切到力矩模式 + 目标 0（不给力，轴可自由转），
      再用手缓慢转动转子轴，看遥测 raw 字段是否跟着变 ——
        raw 明显变 → AS5600 与 I2C 正常，角度恒 0 的原因只剩"FOC 未对齐"
        raw 不动   → 编码器侧问题（磁铁/接线/器件），继续查硬件
    原理：raw = 编码器原始角，**不乘 sensor_direction**；对齐失败时
    shaft_angle 恒 0，raw 却是真实值，所以它是唯一能独立验证编码器的字段。
    """
    bench.send("T0")                      # 力矩模式下 0 = 不给力，轴自由
    bench.send("Z", settle=0.4)           # 让固件把体检快照打到串口
    print(f"\n[体检] 请用手**缓慢转动转子轴**约 {args.duration:.0f} 秒"
          f"（正反各转一点，别用蛮力）...")
    bench.clear()
    bench.pump(args.duration)

    n_raw, span = raw_span(bench.samples)
    print("\n=== 硬件体检结果 ===")
    print(f"  遥测样本 {len(bench.samples)} 条，其中带 raw 字段 {n_raw} 条")
    if n_raw == 0:
        if bench.samples:
            print("  ⚠ 遥测只有 6 段、没有 raw 字段 → 固件是旧版。重烧 closedloop 后再试。")
        else:
            print("  ✗ 没收到任何遥测 —— 先确认固件含 O 命令且已按 RESET")
        return
    span_deg = span * RAD2DEG
    print(f"  raw 跨度 = {span_deg:.1f}°")
    if span_deg > 5.0:
        print("  ✓ 编码器在读数：转轴时 raw 明显变化 → AS5600 与 I2C 正常")
        print("    → 若 motor.shaft_angle 仍恒为 0，唯一原因就是 FOC 未对齐")
        print("      （绝大多数是 12V 未通电；通电后按 RESET，对齐成功即恢复）")
    else:
        print("  ✗ raw 几乎不动（<5°），三种可能：")
        print("      1) 没真的转转子轴（转外壳/风叶不算）")
        print("      2) AS5600 没读到：磁铁偏心或未装、I2C 断线"
              "（看上面的 Z 快照里 I2C 扫描有无 0x36）")
        print("      3) 编码器 / 磁铁损坏")
    save(bench, args, "check", "hardware check / hand-turn encoder test", None)


def cmd_step(bench, args):
    s, idx = run_step_test(bench, args, args.target_deg)
    print(f"\n=== 位置阶跃 {args.target_deg:.0f}° 结果 ===")
    report_step("位置", analyze_step(s, "angle", args.band))
    save(bench, args, f"step{args.target_deg:.0f}deg", f"position step {args.target_deg:.0f} deg", idx)


def cmd_velstep(bench, args):
    s, idx = run_velstep_test(bench, args, args.speed)
    print(f"\n=== 速度阶跃 {args.speed:.2f} rad/s 结果 ===")
    report_step("速度", analyze_step(s, "vel", args.band))
    save(bench, args, f"velstep{args.speed:.1f}", f"velocity step {args.speed:.2f} rad/s", idx)


def cmd_hold(bench, args):
    s, _ = run_hold_test(bench, args, args.speed)
    print(f"\n=== 恒速 {args.speed:.2f} rad/s 稳定性 ===")
    report_hold("速度", analyze_hold(s, "vel"))
    save(bench, args, f"hold{args.speed:.1f}", f"hold {args.speed:.2f} rad/s", None)


def cmd_repro(bench, args):
    """可复现性：同一实验重复 N 次，看关键指标的离散度。"""
    rows, idx = [], None
    for i in range(args.repeat):
        print(f"\n[第 {i + 1}/{args.repeat} 次]")
        if args.test == "velstep":
            s, idx = run_velstep_test(bench, args, args.speed)
            m = analyze_step(s, "vel", args.band)
            name = "速度阶跃"
        else:
            s, idx = run_step_test(bench, args, args.target_deg)
            m = analyze_step(s, "angle", args.band)
            name = "位置阶跃"
        report_step(f"{name} #{i + 1}", m)
        if m.get("ok"):
            rows.append(m)
        time.sleep(0.5)

    if len(rows) < 2:
        print("\n[结论] 有效样本不足，无法评估可复现性")
        return

    print(f"\n=== 可复现性汇总（{len(rows)}/{args.repeat} 次有效）===")
    print(f"  {'指标':<16}{'均值':>12}{'最小':>12}{'最大':>12}{'标准差':>12}")
    for key, label, sc in (("overshoot_pct", "超调 %", 1.0),
                           ("settle_s", "调节时间 ms", 1000.0),
                           ("ss_err", "稳态误差", RAD2DEG),
                           ("ripple_pp", "纹波峰峰", RAD2DEG)):
        vs = [r[key] for r in rows if r.get(key) is not None]
        if len(vs) < 2:
            continue
        print(f"  {label:<16}{_mean(vs) * sc:>12.3f}{min(vs) * sc:>12.3f}{max(vs) * sc:>12.3f}"
              f"{statistics.pstdev(vs) * sc:>12.3f}")
    spread = [r["settle_s"] for r in rows if r.get("settle_s") is not None]
    if spread and _mean(spread) > 0:
        cv = statistics.pstdev(spread) / _mean(spread) * 100
        print(f"\n  调节时间离散度(CV) = {cv:.1f}%  →  "
              + ("良好（<15%），可写进简历" if cv < 15 else "偏大，先查机械/接线是否松动再谈调参"))
    save(bench, args, f"repro_{args.test}", f"repeatability {args.repeat}x {args.test}", idx)


def cmd_selftest(args):
    """无硬件自检：用已知解析解的二阶系统验证指标算法本身。"""
    zeta, wn, t_step_at = 0.5, 20.0, 0.2
    wd = wn * math.sqrt(1 - zeta ** 2)
    samples = []
    steps = int(3.0 / 0.001)
    for i in range(steps):
        tt = i * 0.001
        ts = tt - t_step_at
        if ts < 0:
            y, tgt = 0.0, 0.0
        else:
            y = 1.0 * (1 - math.exp(-zeta * wn * ts) *
                       (math.cos(wd * ts) + zeta / math.sqrt(1 - zeta ** 2) * math.sin(wd * ts)))
            tgt = 1.0
        samples.append(Sample(i, 2, tgt, y, 0.0))

    m = analyze_step(samples, "angle", band=0.02)
    theory_os = math.exp(-math.pi * zeta / math.sqrt(1 - zeta ** 2)) * 100
    theory_rise = (1.0 - 0.4167 * zeta + 2.917 * zeta ** 2) / wn
    theory_settle = 4.0 / (zeta * wn)

    print("=== 指标算法自检（二阶系统 ζ=0.5, ωn=20 rad/s，有解析解可比）===")
    print(f"  {'指标':<14}{'理论(解析)':>14}{'本工具':>14}{'误差':>12}")
    rows = [("超调 %", theory_os, m["overshoot_pct"], theory_os * 0.05),
            ("上升时间 ms", theory_rise * 1000, (m["rise_s"] or 0) * 1000, theory_rise * 1000 * 0.15),
            ("调节时间 ms", theory_settle * 1000, (m["settle_s"] or 0) * 1000, theory_settle * 1000 * 0.15)]
    ok = True
    for label, th, got, tol in rows:
        err = abs(got - th)
        flag = "OK" if err <= tol else "FAIL"
        if err > tol:
            ok = False
        print(f"  {label:<14}{th:>14.2f}{got:>14.2f}{err:>10.2f}  {flag}")
    print(f"\n  阶跃索引检测: ", end="")
    got_idx, got_base = find_step_index(samples)
    want_idx = int(t_step_at / 0.001)
    if got_idx == want_idx and abs(got_base) < 1e-9:
        print(f"正确（检到第 {got_idx} 个样本，基线 target={got_base}）")
    else:
        print(f"异常（期望 {want_idx}，实得 {got_idx}）")
        ok = False
    print(f"\n[自检结果] {'通过 —— 指标算法可信，可以上机' if ok else '未通过 —— 先修算法再上机'}")

    # 顺便验证 CSV/SVG 输出路径，并留下参考图（无硬件也能看到产物长什么样）
    os.makedirs(args.outdir, exist_ok=True)
    csv = os.path.join(args.outdir, "selftest_demo.csv")
    svg = os.path.join(args.outdir, "selftest_demo.svg")
    write_csv(csv, samples, {"test": "selftest", "note": "synthetic 2nd-order step, no hardware"})
    made = write_svg(svg, samples, "selftest demo — synthetic 2nd-order step (zeta=0.5, wn=20)", find_step_index(samples)[0])
    print(f"  参考产物: {csv}")
    print(f"            {svg}" if made else "            (SVG 未生成)")
    return 0 if ok else 1


# ============================================================
def _add_common(p, suppress):
    """公共参数。suppress=True 用于子解析器：不给值时不覆盖顶层已解析的值。

    这样 --port 放在子命令前或后都能用（argparse 的经典陷阱：子解析器默认值
    会覆盖顶层同名属性，必须用 SUPPRESS 规避）。"""
    d = (lambda v: argparse.SUPPRESS) if suppress else (lambda v: v)
    p.add_argument("--port", default=d(None), help="串口，如 COM5；不给则等新口出现")
    p.add_argument("--baud", type=int, default=d(115200))
    p.add_argument("--tel-ms", type=int, default=d(20), help="遥测周期 ms（默认 20 = 50Hz）")
    p.add_argument("--outdir", default=d(DEFAULT_OUTDIR), help="产物目录（CSV/SVG）")
    p.add_argument("--limit", type=float, default=d(None),
                   help="测试前设置电压上限 L（建议固定，保证可比）")
    p.add_argument("--set", action="append", default=d([]), metavar="CMD",
                   help="测试前下发的额外固件命令，可重复。例：--set A15 --set F0.05 --set V8")
    p.add_argument("--no-svg", action="store_true", default=d(False))
    p.add_argument("-v", "--verbose", action="store_true", default=d(False))
    p.add_argument("--quiet", action="store_true", default=d(False), help="不打印固件回显")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common, suppress=True)

    ap = argparse.ArgumentParser(description="F407 闭环调试台 —— 自动跑测试序列、采数、算指标、出图",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="示例：\n"
                                        "  serial_bench.py step --target-deg 90 --port COM5\n"
                                        "  serial_bench.py --port COM5 hold --speed 3 --record 10\n"
                                        "  serial_bench.py repro --test step --target-deg 90 --repeat 10\n"
                                        "  serial_bench.py selftest          (无硬件，验证指标算法)\n")
    _add_common(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("step", parents=[common], help="位置阶跃")
    p.add_argument("--target-deg", type=float, default=90.0)
    p.add_argument("--start-deg", type=float, default=0.0,
                   help="已忽略：阶跃现在以【当前位置】为起点，避免累计角陷阱")
    p.add_argument("--settle", type=float, default=2.0, help="阶跃前稳定时间 s")
    p.add_argument("--pre", type=float, default=1.0, help="基线采集 s")
    p.add_argument("--record", type=float, default=4.0, help="阶跃后采集 s")
    p.add_argument("--band", type=float, default=0.02, help="调节时间判据带（默认 2%%）")

    p = sub.add_parser("velstep", parents=[common], help="速度阶跃")
    p.add_argument("--speed", type=float, default=3.0, help="rad/s")
    p.add_argument("--settle", type=float, default=2.0)
    p.add_argument("--pre", type=float, default=1.0)
    p.add_argument("--record", type=float, default=4.0)
    p.add_argument("--band", type=float, default=0.02)

    p = sub.add_parser("hold", parents=[common], help="恒速稳定性")
    p.add_argument("--speed", type=float, default=3.0)
    p.add_argument("--settle", type=float, default=2.0)
    p.add_argument("--record", type=float, default=10.0)

    p = sub.add_parser("repro", parents=[common], help="可复现性（重复 N 次）")
    p.add_argument("--test", choices=["step", "velstep"], default="step")
    p.add_argument("--repeat", type=int, default=10)
    p.add_argument("--target-deg", type=float, default=90.0)
    p.add_argument("--start-deg", type=float, default=0.0, help="已忽略（同 step）")
    p.add_argument("--speed", type=float, default=3.0)
    p.add_argument("--settle", type=float, default=2.0)
    p.add_argument("--pre", type=float, default=1.0)
    p.add_argument("--record", type=float, default=4.0)
    p.add_argument("--band", type=float, default=0.02)

    p = sub.add_parser("raw", parents=[common], help="原始抓取")
    p.add_argument("--duration", type=float, default=10.0)
    p.add_argument("--start-deg", type=float, default=0.0)

    p = sub.add_parser("check", parents=[common], help="硬件体检：编码器是否在读数（可无 12V）")
    p.add_argument("--duration", type=float, default=6.0, help="转轴采样时长 s")

    sub.add_parser("selftest", parents=[common], help="无硬件自检指标算法")
    return ap


def main():
    args = build_parser().parse_args()
    if not args.cmd:
        build_parser().print_help()
        return 0
    if args.cmd == "selftest":
        return cmd_selftest(args)

    port = args.port or wait_for_new_port(list_ports())
    if not port:
        return 2
    args.port = port
    ser = open_serial(port, args.baud)
    if ser is None:
        return 3

    bench = Bench(ser, verbose=args.verbose, quiet=args.quiet)
    print("=" * 62)
    print("[提示] 若刚上电或刚烧录，请按一下 RESET 再继续（USB CDC 需重新枚举）")
    print("=" * 62, flush=True)
    bench.pump(0.5)
    for line in bench.logs[-6:]:
        print(f"  [dev] {line}")
    bench.logs.clear()

    mode = (2 if args.cmd == "step"
            else 1 if args.cmd in ("velstep", "hold")
            else 0 if args.cmd == "check"      # 力矩模式 + T0 → 轴自由，便于手转
            else None)
    try:
        if not arm_telemetry(bench, args.tel_ms, mode=mode, limit=args.limit):
            return 4
        print(f"[就绪] 遥测 {args.tel_ms}ms，实测 {bench.sample_rate():.1f} Hz"
              f"{'' if args.limit is None else f'，限压 {args.limit}V'}\n")

        extra = getattr(args, "set", None) or []
        if extra:
            for c in extra:
                bench.send(c, settle=0.2)
            print(f"[参数] 已下发: {'  '.join(extra)}")
        bench.clear()

        handler = {"step": cmd_step, "velstep": cmd_velstep, "hold": cmd_hold,
                   "repro": cmd_repro, "raw": cmd_raw, "check": cmd_check}[args.cmd]
        handler(bench, args)
        if bench.bad_lines:
            print(f"  ⚠ 有 {bench.bad_lines} 行遥测解析失败（丢字节/截断，可加大 --tel-ms）")
    except KeyboardInterrupt:
        print("\n[中断] 用户中止")
    finally:
        try:
            # ★ 先恢复安全增益，再关遥测。顺序不能反：
            #   若实验把增益留在失稳点（如 P=2.0），关遥测也救不了——电机仍会持续振荡。
            #   见文件顶部 SAFE_PARK 的说明（已实测踩过，电机剧烈震荡到必须复位）。
            for c in SAFE_PARK:
                bench.ser.write((c + "\n").encode())
                time.sleep(0.03)
            bench.ser.write(b"O0\n")     # 关闭遥测，不留负担给控制环
            time.sleep(0.1)
            bench.ser.close()
            print(f"[收尾] 已恢复安全增益 {' '.join(SAFE_PARK)}，关闭遥测并释放串口")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
