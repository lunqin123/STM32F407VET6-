#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 STM32F407VET6_PeripheralPins.c, 输出 pins.csv (pin, peripheral, signal, af)
这样既能人读, 也能被脚本/表格直接加载, 用来回答"某引脚能接什么外设"之类问题。

用法:
    python parse_pins.py            # 生成 pins.csv
    python parse_pins.py PA_11      # 查询某引脚能映射到的所有外设
"""
import re
import sys
import os

SRC = os.path.join(os.path.dirname(__file__), "STM32F407VET6_PeripheralPins.c")
OUT = os.path.join(os.path.dirname(__file__), "pins.csv")

# 匹配: {PIN, PERIPH, STM_PIN_DATA(...)} // 注释
ROW = re.compile(
    r"\{\s*([A-Z0-9_]+)\s*,\s*([A-Z0-9_]+)\s*,\s*STM_PIN_DATA(?:_EXT)?\s*\(([^)]*)\)\s*\}\s*,?\s*(?://\s*(.*))?"
)

def parse_af(body: str):
    m = re.search(r"GPIO_AF(\d+)_", body)
    if m:
        return "AF" + m.group(1)
    # ADC/DAC 用 0 占位 (无 AF, 模拟模式)
    nums = re.findall(r",\s*(\d+)\s*,", body)
    if nums and nums[0] == "0":
        return "ANALOG"
    return "NONE"

def parse():
    rows = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            m = ROW.search(line)
            if not m:
                continue
            pin, periph, body, comment = m.groups()
            if pin == "NC":
                continue
            af = parse_af(body)
            signal = (comment or "").strip()
            rows.append((pin, periph, signal, af))
    return rows

def main():
    rows = parse()
    if len(sys.argv) > 1:
        q = sys.argv[1].upper()
        print(f"引脚 {q} 可映射的外设:")
        found = [r for r in rows if r[0] == q]
        if not found:
            print("  (在已解析的 PinMap 数组中未找到; 可能该脚是纯电源/复位/OSC 脚)")
        for pin, periph, signal, af in found:
            print(f"  {periph:10s} {signal:18s} {af}")
        return
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write("pin,peripheral,signal,af\n")
        for pin, periph, signal, af in rows:
            f.write(f"{pin},{periph},{signal},{af}\n")
    print(f"已写出 {len(rows)} 行 -> {OUT}")

if __name__ == "__main__":
    main()
