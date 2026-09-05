# STM32F407VET6 项目长期笔记

## 当前主线：运动控制 + 具身智能（视觉伺服云台）

**硬件**（2026-09-04 全部到货）：STM32F407VET6 + C2208-100T 云台电机(120KV/7极对/21.2Ω/12V) + AS5600(I2C 0x36，电机自带) + **SimpleFOC Shield V3.2** + SSD1306 OLED(0x3C) + 12V 适配器 + ST-Link V2。

**★ 驱动板正确型号 = SimpleFOC Shield V3.2（不是 V2.0.4）**（2026-09-04 用户更正 + WebSearch 核实）
| 项 | V2.0.4（旧假设，作废） | V3.2（实际） |
|---|---|---|
| 驱动芯片 | L6234（MKS版实为 IR2104+MOS） | **DRV8313**（2026-09-04 用户看实物丝印确认；MKS 手册所注 DRV8320H 系笔误，作废） |
| 电流采样 | INA240 | ACS712(30A) |
| 引脚名 | IN1/IN2/IN3/EN | **PWM A/B/C + Enable** |
| 跳线帽 | IR2104 跳线帽（12V插左端） | **不存在** |
| 故障指示 | 无 | **Fault 引脚 + 板上 Fault LED** |
| 供电 | 12–35V | **12–30V**（MKS口径） |
| 尺寸 | 68×53mm | 56×53mm |
- 接线影响：仅引脚改名（PA8/9/10 → PWM A/B/C，PB0 → Enable），**接法不变**；少一个跳线帽坑；多一个 Fault LED 当诊断工具
- DRV8313 的 Vih 约 2.0V → **F407 的 3.3V 逻辑可直接驱动，无需电平转换**（比 V2.0.4 更友好）
- **不要接 Shield 排针上的 SDA/SCL**（VDD 焊盘若为 5V 会灌 5V 进 F407 3.3V IO）与 **VIN**（LDO LM7808 启用时输出 8V）
- Fault/Reset：nFAULT 焊盘（官方对应 A3，1=正常/0=故障）；故障后驱动全停，需给 nRESET(A1) 低脉冲复位。**"Enable 已高但电机完全不动"第一嫌疑 = 卡在 fault/reset 态，先看 Fault LED**
- 社区反馈部分 V3.2 批次**排针无引脚标签**；若丝印与文档不符须按实物核对
- **closedloop.cpp 未使用电流采样**（无 linkCurrentSense）→ INA240→ACS712 的变化不影响现有三个固件；将来做力矩环才需配置
**★ 接线铁律**：12V 只碰 Shield VCC/GND；F407 GND 与 Shield GND 必须共地；AS5600/OLED 只能 3.3V（AS5600 不从 Shield 取电，Shield 编码器口可能 5V 会烧）；相序错=抖动不转，交换任意两根。
**接线文档**：`D:\STM32F407VET6\接线指南-硬件到货.md`（七步接线法，每步带验证；含上电前打勾清单与故障定位表）。烧录器：ST-Link SWDIO=PA13 / SWCLK=PA14 / GND / 3.3V。

**代码（全部编译验证过，vendor 库模式）**：
- `firmware-pio/src/`：main.cpp(开环) / selftest.cpp(到货自检) / closedloop.cpp(AS5600 闭环 FOC) / gimbal_tracker.cpp(视觉追踪角度环)
- `vision/vision_tracker.py`：PC 端 OpenCV 追踪 → 串口 `T<弧度>\n` 给 F407
- 引脚：PA8/9/10=PWM(TIM1)，PB0=EN，PB6/7=I2C1，PC13=LED，PA11/12=USB CDC

**关键技术约定**：
- SimpleFOC 锁 2.3.4（2.4.0 的 enableTimerClock 签名与 framework-arduinoststm32 4.30000.0 不兼容）；U8g2 锁 2.36.18
- 库采用 vendor 模式放 `firmware-pio/lib/`（沙箱无法联网装 lib_deps）；platformio.ini 用 build_src_filter 多环境切换（-e main/selftest/closedloop/gimbal/as5600test）
- F407 Wire：PB7=SDA、PB6=SCL（与 UNO 相反！）
- IntelliSense：勿设 C_Cpp.default.compileCommands（会截胡），用 gen_intellisense.py 生成 includePath
- **★ printf 浮点必须加 `-Wl,-u,_printf_float`**（build_flags）：stm32duino 的 newlib-nano 默认裁掉 %f，snprintf 输出浮点为空（症状：OLED 只显示单位没有数字）。**不能写 `-u _printf_float`** —— PIO 会拆成孤立 -u 吞掉 -mcpu，CMSIS 报 "Unknown Arm architecture profile"。AS5600 硬件已实机验证正常（2026-09-04：RAW 读数/AGC=8/MAG=2137 全正常）。
- **★ SimpleFOC Sensor 是 pull 模型**：所有 getter（getMechanicalAngle/getAngle/getVelocity）返回内部缓存，必须有人调 `sensor.update()` 才真读芯片。含 `motor.loopFOC()` 的固件由它自动 pull；**不含 loopFOC 的固件（纯传感器应用如 knob）必须自己每帧调 sensor.update()**，否则读数恒为上电初值。API 区别：getAngle()=连续多圈累计角 / getMechanicalAngle()=单圈 0~2π wrap / getSensorAngle()=原始角。增量式读法（旋钮/滚轮）须用浮点累加器攒步进，取整只在显示层。
- 无动力联调固件：`[env:knob]`（knob.cpp）= AS5600 智能旋钮 0~100；`[env:as5600test]` = 直读寄存器底层诊断。

**沙箱坑（本机开发环境）**：
- pio 全路径 `/c/Users/16689/.platformio/penv/Scripts/pio.exe`
- 沙箱 SAFE_DELETE 拦截 `.platformio` 锁文件批量删除 → pio 装库失败（仅噪音，不影响编译）
- **编译报 `SHFileOperationW 失败: 0x2` 的正解**：`rm -rf .pio/build/<env>`（只删单个环境目录，勿删整个 .pio）+ `dangerouslyDisableSandbox: true` + 前台运行
- **★ platformio.ini 禁用 `build_cache_dir`**（2026-09-06 实测）：SCons CacheDir 初始化要创建+删除自己的锁文件，沙箱删除保护拦截 os.unlink → 所有环境编译开始前直接 FAILED（错误栈在 env.CacheDir）。已回滚，platformio.ini 留有注释。跨环境编译缓存与本机沙箱不兼容，勿再启用
- PowerShell 的 `Remove-Item -Recurse -Force` **无效**（静默失败），必须用 Bash `rm -rf`
- 全量重编耗时约 150s → timeout 需 ≥ 300000，否则被 SIGTERM；后台任务不获越权批准
- 过滤噪音：`pio run 2>&1 | grep -v "safe-delete" | tail -30`
- **Git Bash 的 `/d/xxx` 路径不能直接传给 Windows python**，会变成 `d:\d\xxx`（ENOENT）。先 `cd` 再用相对路径，或传 `D:/xxx`

**串口通道（已实测）**：
- `Serial` = **USB CDC**，走 PA11/PA12 的板载 USB 口（`platformio.ini` 有 `-D PIO_FRAMEWORK_ARDUINO_ENABLE_CDC`）。**ST-Link 不提供串口** → 不插 USB 线就没有任何 COM 口。
- 烧录与串口互不冲突：upload 走 SWD(PA13/PA14)，打印走 USB。
- 工具：`firmware-pio/tools/serial_capture.py`（managed python 自带 pyserial 3.5），支持等新 COM 口出现 / `--send` 自动发菜单命令。
- **无串口时的验证通道**：`selftest.cpp` 的 `setup()` 上电自动跑 scanI2C→testOLED→testLED。按 RESET 后看 PC13 LED 闪 5 下 + OLED 上 `I2C found: N`（N=2 表示 OLED 与 AS5600 都在）。
- **★★ STM32 USB CDC 必须 `ser.setDTR(True)`**：DTR 为低时固件不发数据，表现为"COM 口正常枚举、TX 成功、收到 0 字节"。F407 的 CDC 是 **VID:PID = 0483:5740**。USB CDC 波特率虚设，零字节不要怀疑波特率。
- **自检实机结果（2026-09-04 首次）**：I2C 扫到 `0x36 AS5600` + `0x3C OLED` 共 2 个设备；OLED 输出正常；LED 闪 5 次。→ ST-Link 链路、F407 供电、PB6/PB7 I2C 全部正确。

**规划**：P1 单轴视觉追踪(PC+摄像头) → P2 两轴(第2个C2208+支架) → P3 OpenMV/K210 边缘化视觉。
**学习路线（2026-09-03 制定）**：详见 `D:\STM32F407VET6\学习路线图.md`——6阶段（控制理论补课→P1跑通→FOC深化→ROS2+Linux副技能→LeRobot具身智能→两轴+边缘化），每周10-15h，含已核实的B站资源（江协PID BV1G9zdYQEr3、灯哥开源SimpleFOC、鱼香ROS BV19U4y1n7CQ、赵虚左 BV1VB4y137ys、DR_CAN space 230105574）与简历里程碑表。纪律：动手:看视频≥1:1，每阶段产出可见物。
**电源结论（2026-09-04 反接事故后修正）**：原"12V 适配器方案"作废。改用**带限流(CC)的可调直流电源 3-24V / 0-5A**，核心价值是预设限流（如 0.5A）防止再次烧毁。纯调电压无 CC 的便宜货无保护价值。用户已确认采购此规格。
