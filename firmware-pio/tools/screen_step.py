#!/usr/bin/env python
"""
整定筛选器 —— 对多组参数各跑 N 次阶跃，输出紧凑对比表。

【为什么需要它（本项目实测结论）】
本机系统在"推荐增益"附近处于**稳定边界**：同一组参数会随机给出
「干净衰减到 0.0°」或「自持 18~40° 极限环」两种截然不同的结果。
  · 实测证据：P0.5/I10/F0.03 在 18:29 那次包络 64→16.6→7.5→0.9→0.0（衰减）；
    在 18:51 那次包络 97→15.2→18.4→21.8→17.1→19.5→18.9（自持）。
→ 因此**单次跑出来的漂亮数字等于侥幸**。判断一个工作点是"稳健"还是"侥幸"，
  必须看多次重复的 min/max 离散度。本脚本就是干这个的。

【用法】
  # 不覆盖参数（用固件默认值）
  python screen_step.py --port COM8 --limit 2 --repeat 3 --cfg "-"

  # 多组对比（多条固件命令用 ; 分隔）
  python screen_step.py --port COM8 --limit 2 --repeat 3 \
      --cfg "-" --cfg "F0.01" --cfg "I5" --cfg "F0.01;I5" --cfg "A10"

【判读】
  看每组 "纹波峰峰" 的 最小/最大：
    · 最大也 < 1°        → 稳健（可以去做 E6）
    · 最小 ≈ 0 而最大很大 → **双稳态/临界**，这组不能用，别被均值骗了
  只看均值会得出"这组挺稳"的错误结论 —— 这正是我们要避免的。
"""

import argparse
import os
import re
import subprocess
import sys

BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serial_bench.py")
KEYS = ("上升时间", "超调", "调节时间", "稳态误差", "纹波峰峰")


def run_cfg(cfg, a):
    cmd = [sys.executable, "-u", BENCH, "repro", "--test", "step",
           "--target-deg", a.target_deg, "--repeat", str(a.repeat),
           "--port", a.port, "--limit", a.limit, "--quiet"]
    if cfg and cfg != "-":
        for c in cfg.split(";"):
            if c.strip():
                cmd += ["--set", c.strip()]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    keep, table = [], {}
    for ln in r.stdout.splitlines():
        s = ln.strip()
        if s.startswith("→") or "无法评估" in s or "CV =" in s:
            keep.append(s)
        elif any(s.startswith(k) for k in KEYS):
            keep.append(s)
            # 指标行格式：名称 均值 最小 最大 标准差 [CV]
            nums = re.findall(r"-?\d+\.\d+", s)
            if len(nums) >= 4:
                table[s.split()[0]] = nums
    return keep, table


def main():
    ap = argparse.ArgumentParser(description="多组参数各跑 N 次阶跃，紧凑对比")
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--limit", default="2")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--target-deg", default="90")
    ap.add_argument("--cfg", action="append", default=[],
                    help="一组参数（固件命令用 ; 分隔）；'-' 表示用固件默认值")
    a = ap.parse_args()
    if not a.cfg:
        a.cfg = ["-"]

    results = []
    for cfg in a.cfg:
        print(f"\n########## 配置: {cfg} （{a.repeat} 次） ##########", flush=True)
        keep, table = run_cfg(cfg, a)
        for ln in keep:
            print("  " + ln, flush=True)
        results.append((cfg, table, keep))

    print("\n" + "=" * 78)
    print("汇总（关键看『纹波峰峰』的最小~最大 与 『调节时间』的最小~最大）")
    print("=" * 78)
    print(f"{'配置':<16}{'纹波 均值/最小/最大':<30}{'调节(ms) 均值/最小/最大':<30}")
    for cfg, t, _ in results:
        rp = t.get("纹波峰峰", ["-"] * 4)
        cd = t.get("调节时间", ["-"] * 4)
        r = f"{rp[0]}/{rp[1]}/{rp[2]}" if len(rp) >= 3 else "-"
        c = f"{cd[0]}/{cd[1]}/{cd[2]}" if len(cd) >= 3 else "-"
        print(f"{cfg:<16}{r:<30}{c:<30}")
    print()
    for cfg, t, keep in results:
        v = next((k for k in keep if k.startswith("→")), "(未产出结论)")
        print(f"  {cfg:<16} {v}")
    print("\n提示：纹波『最大』也 <1° 才算稳健；最小≈0 而最大很大 = 双稳态，这组不能用。")


if __name__ == "__main__":
    sys.exit(main())
