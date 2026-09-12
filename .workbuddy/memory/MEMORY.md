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
- **★★ SimpleFOC 铁律：不要重复调用 `motor.shaftAngle()` / `shaftVelocity()`**（2026-09-12 发现并修复）。二者内部会推进 `LPF_angle` / `LPF_velocity` 滤波器状态——每循环多调一次就等于多滤一次，**既改变控制环行为、又使显示/日志数据与控制器实际用值不一致**。正确做法：读 **public 缓存成员 `motor.shaft_angle` / `motor.shaft_velocity`**（`BLDCMotor::move()` 每周期已刷新，库注释明确 "read value even if motor is disabled to keep the monitoring updated"）。`gimbal_tracker.cpp` 已自行规避；`closedloop.cpp` 2026-09-12 修了 3 处。
- **★ 闭环固件的周期性串口输出必须"非阻塞"**（2026-09-12 确立）：USB CDC 的 `write()` 在主机不读、缓冲满时会阻塞，与"OLED 全帧刷新致顿挫"是同一类问题。规范做法：**单缓冲一次 write + 先查 `Serial.availableForWrite()`，缓冲不够就丢样本并计数**（丢样本可接受，阻塞不可接受）。不要用内置 `motor.monitor()`（按变量分多次 print，库源码自注 "significantly slowing the execution down"）。遥测**默认关闭**。
- 无动力联调固件：`[env:knob]`（knob.cpp）= AS5600 智能旋钮 0~100；`[env:as5600test]` = 直读寄存器底层诊断。
- **★ 调试台（2026-09-12 新建）：`firmware-pio/tools/serial_bench.py`** —— 自动跑测试序列 → 采遥测 → 算指标 → 出 CSV + SVG，**零额外依赖**（本机无 numpy/matplotlib，图自绘）。子命令：`step` / `velstep` / `hold` / `repro` / `raw` / `selftest`。`--set` 可下发任意固件命令，使整定矩阵可脚本化。**`selftest` 无需硬件即验证指标算法本身**（解析解二阶系统 ζ=0.5 → 理论超调 16.30%，实测精确吻合）——上位机工具不必"等上机才知道准不准"。阶跃时刻由**遥测 `target` 字段变化**在设备侧判定，不受 USB 延迟影响。
- 遥测协议（closedloop）：`O<ms>` 开启（默认关，最小 5ms），输出 `D,<ms>,<mode>,<target_rad>,<angle_rad>,<velocity_rad_s>`；整定配套命令 `A<数字>`=位置环 P、`V<数字>`=速度上限。
- **整定手册：`firmware-pio/调试台与整定矩阵.md`** —— E1 电压上限 → E2 速度低通 F（含 **F 0.05→0.1 的双指标定稿法**：hold 找纹波拐点 + velstep 确认调节时间未恶化 >20%）→ E3 速度环 P → E4 速度环 I → E5 位置环 A/V → E6 可复现性验收（CV<15%）；含记录表模板、三档验收标准（L1/L2/L3）与安全清单。方法学：**OFAT，一次只改一个参数**。

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

**★ 烧录铁律（2026-09-06 实测）：12V 开着烧录必失败——PWM 开关噪声毁 SWD（报 "Failed to write memory"/verify 随机错位），烧录前必须关电源输出。OpenOCD 反复失败时换 CubeProgrammer CLI：`STM32_Programmer_CLI.exe -c port=SWD -d firmware.elf -v -rst`（官方驱动栈，实测可靠）；远程复位 `-c port=SWD -rst` 可配合后台 serial_capture 无人值守抓启动横幅。**

**★ OLED 与电机控制循环（2026-09-06 确诊）：全帧 sendBuffer 1KB 阻塞 ~25-30ms，10Hz 定时刷新 = 每秒冻结 10 次 = 电机顿挫（闭环更敏感）。解法两级：main.cpp 事件驱动（参数变才画）；closedloop.cpp 两段式（静态区事件驱动 + 动态区 updateDisplayArea 局部发 256B/4Hz）。任何含 move()/loopFOC() 的主循环禁止周期性全帧刷新。**

**★★ "角度恒为 0" 根因与自诊断（2026-09-12 源码级确诊，已解决）**：
- 根因链：**12V 未通电 → `alignSensor()` 测不到转动 → 返回 0 → `sensor_direction` 保持 `Direction::UNKNOWN(=0)` → `shaftAngle() = 0 × getAngle() = 0`**；且 `initFOC()` 失败分支会调 `disable()` → driver enabled=0，之后电机也不会转
- **`shaft_angle` 恒 0 与"编码器损坏"症状完全一致**，不可区分 → 必须看**编码器原始角 raw**（不乘 sensor_direction）
- **`loopFOC()` 第一行就 `sensor->update()`，先于 `enabled` 判断**（BLDCMotor.cpp）→ 即便驱动关闭，raw 依然新鲜 → **无 12V 也能验编码器**
- 固件已加 `Z` 命令 + 上电自动体检 `printDiag()`（I2C 扫描/方向/zero_electric_angle/motor_status/driver enabled/raw/shaft_angle + 结论行）
- 遥测 D 行为 **7 段**：`D,<ms>,<mode>,<tgt>,<ang>,<vel>,<raw>`（旧 6 段仍兼容解析）
- 工具 `serial_bench.py` 新子命令 **`check`**：力矩模式+`T0` 让轴自由 → 手转转轴看 raw 跨度（>5° 即编码器正常）。**上机第一步永远先跑它**
- 实测证据：`raw=5.4778 rad` + `I2C: 0x36 0x3C` + `status=0xE` + `enabled=0` → 编码器完好，**唯一缺对齐动力，通电+RESET 即解决**
- **远程复位替用户按 RESET**：`STM32_Programmer_CLI.exe -c port=SWD -rst`（12V 开着也可用，因驱动 enabled=0 无 PWM 噪声）；复位后 CDC 仍枚举回同一 COM

**★★ 首次闭环实测（2026-09-12）：L1 达标 + 欠阻尼待收敛**
- 通电+复位后 `Z` 体检通过（`direction=CW`、`zero_electric_ang=5.168`、`ready`、`enabled=1`、`shaft_angle==raw`）
- **90° 阶跃 L1 达标且远超**：上升 160ms / 超调 0.1% / 调节 280ms / 稳态误差 **-0.054°** / 纹波 pp 0.17°；200Hz 复跑一致 → 可复现性好
- **但位置环欠阻尼**：5° 小阶跃 **超调 54.7%**；90° 瞬态实为 ~8° 幅度衰减振荡（0→46→38→66→63→76→73）。大信号被 `velocity_limit` 饱和掩盖，**标准超调指标看不出来**
- **静止时有量化极限环**：angle 精确在 ±0.001500 rad（**±1 LSB**）间 ~50Hz 交替，vel 同步翻符号 → 12bit 编码器+差分测速固有现象，靠 `F` 抑制
- **★ 度量纪律**：**不要用相邻遥测样本差分算"瞬时速度"**（量化 0.088°/LSB + 间隔抖动 5~12ms 会放大数倍）。可信的只有 ①角度轨迹 ②固件侧指标。编码器实际每 5ms 更新（非采样饥饿）
- 对照 `--set V3` → 稳态误差 **0.0000°**、纹波 RMS 0.075→0.029 → 证明 `velocity_limit` 生效，抖动源自增益/饱和而非 I2C
- **★★ 重大更正：上述瞬态数据是在"电机定子未固定"状态下测的 → 不可信**。扭矩相互：转子加速的反作用力矩推定子，定子靠线缆约束 = 扭簧-质量系统 → 低频振荡。**"8° 衰减振荡"与 5° 阶跃 54.7% 超调很可能主要是机械伪影，而非控制欠阻尼**。稳态指标（误差 -0.054°、纹波 ±1 LSB）大概率仍有效（静止无反作用力矩）
- **★ 纪律教训**：**调参前先确认机械边界条件**（定子是否固定、轴是否自由、有无负载）。我上一轮直接归因"位置环欠阻尼"属于**过早归因**——应先问"电机固定了吗"。定子必须固定（夹/胶/扎带），转子必须自由
### ★★ 首次整定实测结论（2026-09-12，定子固定后）
- **固件默认增益在正确机械条件下不稳定**：90° 阶跃出现 ~20Hz、±21° 持续振荡（纹波 pp **43.5°**）
- **`A` 与 `F` 都不是杠杆**（A20→3 反而更差；I=20 时改 F 无效）→ **真正的杠杆是速度环积分 `I`**
- **`I=20` 不稳 ; `I=10` 稳定**（纹波 43.5° → **0.172° = 2 LSB 量化地板**）；`I=2` 欠增益静差大
- **P 扫描**：P0.5 最优（调节 1194ms、超调 0.0%）；P≥1.0 又失稳
- **推荐定稿**：`L=2V, F=0.03, P=0.5, I=10, A=20, V=10` → 超调 0.0% / 调节 1194ms / 稳态误差 +0.066° / 纹波 0.26° → **L1 达标**
- **★ 教训 1**：机械边界条件是调参前置条件（定子固定！）—— 未固定时"超调 0.1%、上升 160ms"是假象（定子反扭吸收振荡）。**稳态指标两种情况下都可信（静止无反作用力矩），瞬态指标不可信**
- **★ 教训 2**：测试序列不能污染初始条件。`hold` 后电机已在第 N 圈，此时若 `step` 用绝对角 0 当起点 → 电机倒转几十圈、`--settle` 来不及 → 会被误判成"失控/极限环"（实测角度跑到 6513°~9393°）。**已修复：`step` 改为相对当前位置阶跃；`raw` 先 `M2` 再守住当前位置**
- **★ 排障方法论**：振荡时先判别"**哪个环**在振荡"（速度模式下 A 不参与 → 排除位置环），再找杠杆。我先调了两轮 `A` 才想到，属走弯路
- 待办：E2（F 定稿）、E5（扫 A 与 V）、E6（repro CV<15%）

**★ 沙箱：`rm -rf .pio/build/<env>` 会被 SAFE_DELETE 拦截**（count>50 需批量确认）→ 用 `&&` 串联时短路，pio 根本不执行。实测**不预删也能正常增量编译**（只重编改动文件），非必要不要预删。

**规划**：P1 单轴视觉追踪(PC+摄像头) → P2 两轴(第2个C2208+支架) → P3 OpenMV/K210 边缘化视觉。
**学习路线（2026-09-03 制定）**：详见 `D:\STM32F407VET6\学习路线图.md`——6阶段（控制理论补课→P1跑通→FOC深化→ROS2+Linux副技能→LeRobot具身智能→两轴+边缘化），每周10-15h，含已核实的B站资源（江协PID BV1G9zdYQEr3、灯哥开源SimpleFOC、鱼香ROS BV19U4y1n7CQ、赵虚左 BV1VB4y137ys、DR_CAN space 230105574）与简历里程碑表。纪律：动手:看视频≥1:1，每阶段产出可见物。
**电源结论（2026-09-04 反接事故后修正）**：原"12V 适配器方案"作废。改用**带限流(CC)的可调直流电源 3-24V / 0-5A**，核心价值是预设限流（如 0.5A）防止再次烧毁。纯调电压无 CC 的便宜货无保护价值。用户已确认采购此规格。

## 学习文档双轨制（2026-09-12 确立）
- **《底层经典问题集.md》= 社招面试题库**（**93 题 / 12 模块**，求职期主力）。用户方向：**先找工作，再深挖工程**——面试口径 ≠ 工程口径，题目按"面试官会怎么问+怎么追问"组织
- **《底层工程实践问题集.md》= 工程实战题库**（49 题，绑本项目实锚，就业后用）；《底层学习路线.md》的 9 层"配套练习"链接指向此文件
- 面试素材铁律：**用户亲历事故是最好的社招答案**（烧录噪声毁 SWD / OLED 阻塞顿挫 / 量化噪声嗡嗡 → 已固化为"排障过程"型标准答案）。新事故发生时优先按"症状→假设→证伪→根因→对策→沉淀"格式存档
- **★ 面试题库已做事实核查（2026-09-12）**：修正 1 处实质性错误 + 标注 2 处口径风险。**教训：涉及"某芯片有无某特性"必须查对应型号 RM/TRM，社区共识（尤其"F4 没位带"）很可能是错的**。已核实结论：
  - **STM32F4 有**位带（RM0090 §2.3.3，外设+SRAM 都映射）；**无位带的是 Cortex-M7（F7/H7）与 M0/M0+**
  - 中断延迟：进入 ≤12 / 尾链 6 周期为确定项；**返回周期官方有 10/12 两种表述**（ARM TRM 写 12，Yiu 书写 10）→ 面试说"约 12 + 尾链 6"
  - STM32F4 **无**硬件 ADC 过采样（F3/L4/F7/H7 有）；F4 的 CCM RAM **不能被 DMA 访问**

## 学习文档 · 前沿趋势模块（2026-09-12 新增）
- `底层经典问题集.md` **模块十一 前沿趋势与行业认知（Q84~Q93）** + **11.1 趋势速记卡**（8 主题 8 组可报数字）——用于主管面/交叉面的"行业方向"提问；冲刺表扩至 **18 天**
- 四条主线：**AI 下沉 MCU（Physical AI）／安全合规立法（EU CRA 2027）／RISC-V 分场景替代／软件定义 + RTOS 生态集中化**
- 方向匹配要点：用户目标 = 运动控制 + 机器人 + 具身智能；趋势模块把"行业趋势 → 岗位要求 → 用户要补什么"做成对照表（Q93），面试可直接说"我注意到行业在 X，所以我在补 Y"

## 用户在职项目（2026-09-12 披露）——简历核心资产
- **双核嵌入式系统**：通信核跑 RTOS（任务/协议/参数管理）+ 功率核为裸机（用户称 **EOS**，按无 OS 理解；若为具体框架需再确认），承担 xx kHz 硬实时控制与保护
- 面试价值：**双核经历是差异化优势**（多数候选人为单核背景）；简历可写但必须**脱敏**（不谈产品/客户/内部代号/参数/代码）
- 对应题库：`底层经典问题集.md` 模块十 Q70~Q83（含简历模板与话术骨架）；用户尚需补：多核启动/IPC/Cache 一致性/跨核互斥/实时性度量
