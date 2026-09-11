# STM32F407VET6 · 运动控制 + 具身智能 学习项目

> 主线：**用 F407 + SimpleFOC 做视觉伺服云台，一路通向具身智能**（LeRobot / VLA）。
> 方向定于 2026-09-02（否决边缘 AI 路线，依据见 `archive/边缘AI路线-旧文档/`）。
> 完整学习规划 → [`学习路线图.md`](学习路线图.md)

---

## 一页目录地图

```
STM32F407VET6/
├── 学习路线图.md            ★ 主线规划（6 阶段 / 资源 / 里程碑表）
├── 底层学习路线.md           ★ 副线：寄存器→体系结构→手写 FOC（9 层 / 每层真机任务+自测题）
├── 底层经典问题集.md         ★ 社招面试题库（93 题 / 12 模块，含双核专题+前沿趋势，求职期主力）
├── 底层工程实践问题集.md      工程实战题库（49 题 / 绑本项目实锚，就业后深挖用）
├── 硬件采购清单.md           ★ 已购硬件 + 接线表 + 电源结论
├── 接线指南-硬件到货.md       ★ 七步接线法（每步带验证）+ 故障定位表
│
├── firmware-pio/            现役固件（PlatformIO，见下方环境表）
├── vision/                  PC 端视觉追踪脚本（OpenCV → 串口）
├── 库函数学习/               ★ 库函数字典（Arduino/Wire/SimpleFOC/U8g2 逐个函数+示例+坑）
├── 00-CHIP-REFERENCE/       芯片权威资料（SVD 中文版 / HAL 手册 / 引脚表）
│
├── 01-stage0-tinyml/        【旧路线遗留，勿动】venv 内是绝对路径
├── pictures/                （空，备用）
└── archive/                 【全部历史归档，零删除，见其 README】
```

## 固件环境速查（`firmware-pio/`）

| 环境 | 源文件 | 用途 |
|---|---|---|
| `main` | `src/main.cpp` | 开环速度控制（上电即转，`T<rad/s>` 设速 / `L<V>` 调压） |
| `selftest` | `src/selftest.cpp` | 硬件自检（I2C / OLED / LED / PWM，上电自动跑） |
| `closedloop` | `src/closedloop.cpp` | AS5600 闭环 FOC |
| `gimbal` | `src/gimbal_tracker.cpp` | P1 视觉追踪角度环（PC 发 `T<rad>`） |
| `as5600test` | `src/as5600test.cpp` | AS5600 底层诊断（直读寄存器） |
| `knob` | `src/knob.cpp` | 无动力智能旋钮（自动息屏 + 转动唤醒） |

编译烧录（ST-Link）：

```bash
cd firmware-pio
pio run -e <环境名>            # 编译（约 60-80 秒）
pio run -e <环境名> -t nobuild -t upload    # 烧录（约 9 秒）
```

串口 = 板载 USB 口（ST-Link 不带串口），COM 口看 VID:PID `0483:5740`。
串口工具：`firmware-pio/tools/serial_capture.py`。

## 当前状态板（2026-09-06）

| 项目 | 状态 |
|---|---|
| 硬件接线（七步法步骤 1–5） | ✅ 全部验证通过（PWM 七点 0.66V） |
| AS5600 / OLED / I2C 链路 | ✅ 实测正常（0x36 + 0x3C） |
| 串口命令链路 | ✅ T / M / L / P 命令全通 |
| 无动力智能旋钮 | ✅ 可用（自动息屏 + 兜底重推黑帧） |
| **电机开环转动** | ✅ 2026-09-06 首转成功（T2/T5/T8 调速通过） |
| **闭环 FOC 速度环** | ✅ 2026-09-06 达成：转速丝滑 + 捏轴伺服对抗验证通过 |
| Shield V3.2 健康状况 | ✅ 实机验证完好 |
| 位置环 + 8 档棘轮 | ⏭ 下一步：M2 位置模式 → knob.cpp 加社区三行力控公式 |
| P1 视觉追踪 | ⏭ 软件链路已调通，接摄像头即联调 |

**★ 烧录铁律（今日实测）：12V 开着烧录必失败（PWM 噪声毁 SWD 校验）——烧录前必须关电源输出。**
**★ 烧录通道：OpenOCD 不稳时改用 CubeProgrammer CLI（已验证可靠）：
`STM32_Programmer_CLI.exe -c port=SWD -d firmware.elf -v -rst`**

## 下一步清单

1. ~~Shield 空载测试~~ → ✅ 2026-09-06 实机转动验证通过
2. ~~开环转动验收~~ → ✅ 2026-09-06 通过（T2/T5/T8 调速正常）
3. **闭环 + 8 档棘轮**：closedloop 烧录 → initFOC 对齐（上电会轻动一下）→ knob.cpp 加社区三行力控公式做 detent（公式在 `库函数学习/06`）
4. **P1 视觉追踪**：摄像头 + `vision/vision_tracker.py --port COM8`，软件链路已全部调通

> ⚠️ 硬件注意：新电源**没有电流显示表**（只有电压表）。首次闭环前建议 CC 限流仍设保守值；如需精确电流观测，可后续加一个串联直流电流表头。

## 已踩过的坑（速查，详见项目笔记）

- 12V 反接 = 全链路短路（适配器烧毁救了下游）→ 上电前万用表确认极性
- printf 浮点必须 `-Wl,-u,_printf_float`（写成 `-u xxx` 会吞掉 `-mcpu`）
- SimpleFOC 传感器是 pull 模型：没有 `motor.loopFOC()` 就必须自己调 `sensor.update()`
- U8g2 与其他 I2C 设备共总线：**禁用 `setPowerSave`**，熄屏用全黑帧
- ST-Link 烧不动（`LIBUSB_ERROR_ACCESS`）= 拔了重插
- 构建被 `SHFileOperationW 失败 0x2` 拦 = `rm -rf .pio/build/<env>` 全量重编
