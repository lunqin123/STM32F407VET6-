#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 .vscode/c_cpp_properties.json，让 VSCode 能找到 Arduino.h / SimpleFOC.h / U8g2lib.h 等。

为什么需要它：
  PlatformIO 的 `pio run -t compiledb` 生成的 compile_commands.json **不包含依赖库
  的 include 路径**（已知问题），所以只靠它会画红线。本脚本把
  compile_commands.json 里的框架路径 + .pio/libdeps 下的库路径 + Arduino 框架自带库
  （Wire / SPI / EEPROM 等）合并，生成一份完整的 c_cpp_properties.json。

配套设置（.vscode/settings.json）：
  ★ 千万不要设 C_Cpp.default.compileCommands！一旦设了，C/C++ 扩展会优先用那份
    缺库的 compile_commands.json、忽略本脚本生成的 includePath，红线照样在。
    只设 compilerPath / 标准 / defines 即可，让扩展走 c_cpp_properties 的 includePath。

何时重跑：新增/更换 lib_deps 里的库、或新增 PlatformIO 环境之后，重新执行一次。

用法：
    python gen_intellisense.py
"""
import json
import os
import shlex

PROJ = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(PROJ)                      # 工作区根（D:\STM32F407VET6）

CC = os.path.join(PROJ, "compile_commands.json")
LIBDEPS = os.path.join(PROJ, ".pio", "libdeps")   # 下挂各环境目录，全部扫描
LIB = os.path.join(PROJ, "lib")                    # 项目内置(vendored)库目录
OUT = os.path.join(WS, ".vscode", "c_cpp_properties.json")

TOOLCHAIN = os.path.join(
    os.path.expanduser("~"), ".platformio", "packages",
    "toolchain-gccarmnoneeabi", "bin", "arm-none-eabi-gcc.exe",
).replace("\\", "/")


def collect_from_compiledb():
    """从 compile_commands.json 提取框架/系统的 include 路径。"""
    paths = set()
    if not os.path.exists(CC):
        print("[警告] 未找到 compile_commands.json，请先运行：pio run -t compiledb")
        return paths
    with open(CC, encoding="utf-8") as f:
        data = json.load(f)
    for entry in data:
        try:
            parts = shlex.split(entry.get("command", ""), posix=False)
        except Exception:
            parts = entry.get("command", "").split()
        for i, tok in enumerate(parts):
            if tok == "-I" and i + 1 < len(parts):
                paths.add(parts[i + 1].strip('"'))
            elif tok.startswith("-I") and len(tok) > 2:
                paths.add(tok[2:].strip('"'))
    return paths


def collect_libs():
    """补充 compiledb 漏掉的依赖库路径。
    同时扫描两处：
      - 项目 lib/ 下内置(vendored)的库（SimpleFOC / U8g2 ...）
      - .pio/libdeps 下各环境目录（如果用联网 lib_deps 安装的库）"""
    paths = set()
    # 1) vendored 库（lib/<LibName>/，标准 Arduino 结构有 src/）
    if os.path.isdir(LIB):
        for name in os.listdir(LIB):
            base = os.path.join(LIB, name)
            if not os.path.isdir(base):
                continue
            src = os.path.join(base, "src")
            paths.add(src if os.path.isdir(src) else base)
    # 2) .pio/libdeps（联网安装时）
    if os.path.isdir(LIBDEPS):
        for env in os.listdir(LIBDEPS):
            env_dir = os.path.join(LIBDEPS, env)
            if not os.path.isdir(env_dir):
                continue
            for name in os.listdir(env_dir):
                base = os.path.join(env_dir, name)
                if not os.path.isdir(base):
                    continue
                src = os.path.join(base, "src")
                paths.add(src if os.path.isdir(src) else base)
    return paths


def collect_framework_libs():
    """补充 Arduino 框架自带的库（Wire / SPI / EEPROM 等）。
    这些不在 compile_commands.json 里，也不在 .pio/libdeps 下，容易漏。"""
    paths = set()
    root = os.path.join(
        os.path.expanduser("~"), ".platformio", "packages",
        "framework-arduinoststm32", "libraries",
    )
    if not os.path.isdir(root):
        return paths
    for name in os.listdir(root):
        src = os.path.join(root, name, "src")
        if os.path.isdir(src):
            paths.add(src)
    return paths


def main():
    inc = collect_from_compiledb() | collect_libs() | collect_framework_libs()
    # 规范化 + 去重，排除不存在的路径
    inc = sorted({p for p in (os.path.normpath(x) for x in inc) if os.path.isdir(p)})

    cfg = {
        "configurations": [
            {
                "name": "STM32F407VET6 (PlatformIO)",
                "includePath": inc,
                "defines": [
                    "PLATFORMIO=60119",
                    "STM32F407xx",
                    "STM32F4",
                    "STM32F4xx",
                    "ARDUINO=10808",
                    "ARDUINO_ARCH_STM32",
                    "ARDUINO_GENERIC_F407VETX",
                    "HAL_UART_MODULE_ENABLED",
                    "USE_HAL_DRIVER",
                    "NDEBUG",
                ],
                "compilerPath": TOOLCHAIN,
                "cStandard": "c11",
                "cppStandard": "c++17",
                "intelliSenseMode": "gcc-arm",
                "compilerArgs": [
                    "-mcpu=cortex-m4",
                    "-mthumb",
                    "-mfpu=fpv4-sp-d16",
                    "-mfloat-abi=hard",
                ],
            }
        ],
        "version": 4,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    print(f"已生成: {OUT}")
    print(f"include 路径总数: {len(inc)}")
    print("\n关键头文件可达性：")
    # 注意：必须递归找。SimpleFOC 的头文件在子目录里
    # （drivers/BLDCDriver3PWM.h、communication/Commander.h），
    # 只查一层会误报"缺失"。
    for h in ["Arduino.h", "Wire.h", "SimpleFOC.h", "BLDCMotor.h",
              "BLDCDriver3PWM.h", "Commander.h", "U8g2lib.h"]:
        hit = None
        for d in inc:
            for dirpath, _, files in os.walk(d):
                if h in files:
                    hit = os.path.join(dirpath, h)
                    break
            if hit:
                break
        print(f"  [{'OK' if hit else '缺失':^4}] {h}" + (f"  <- {hit}" if hit else ""))


if __name__ == "__main__":
    main()
