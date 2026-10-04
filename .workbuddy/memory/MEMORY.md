# STM32F407VET6 项目长期笔记

## 一、硬件与接线

**硬件**（2026-09-04 全部到货）：STM32F407VET6 + C2208-100T 云台电机(120KV/7极对/21.2Ω) + AS5600(I2C 0x36) + **SimpleFOC Shield V3.2** + SSD1306 OLED(0x3C) + 12V 电源 + ST-Link V2。
**接线文档**：`接线指南-硬件到货.md`（七步接线法 + 上电前打勾清单 + 故障定位表）。ST-Link: SWDIO=PA13 / SWCLK=PA14。
**引脚分配**：PA8/9/10=PWM(TIM1)，PB0=Enable，PB6=SCL/PB7=SDA(I2C1)，PC13=LED，PA11/12=USB CDC，A3=nFAULT，A1=nRESET。

**Shield V3.2（正确型号，非 V2.0.4）**：DRV8313 + ACS712，引脚名 PWM A/B/C + Enable，无跳线帽，带 Fault 引脚与板上 Fault LED。社区反馈部分批次排针无丝印标签 → 须按实物核对。DRV8313 的 Vih≈2.0V → F407 的 3.3V 可直接驱动，无需电平转换。
- **不要接 Shield 排针上的 SDA/SCL**（VDD 焊盘可能 5V，会灌进 F407）与 **VIN**（LM7808 启用时输出 8V）
- "Enable 已高但电机完全不动"第一嫌疑 = 卡在 fault/reset 态，**先看 Fault LED**，再给 nRESET 低脉冲
- 现有固件未用电流采样（无 `linkCurrentSense`），做力矩环才需配置

**★ 接线铁律**：12V 只碰 Shield VCC/GND；F407 与 Shield 必须共地；AS5600/OLED 只能 3.3V；相序错=抖动不转，交换任意两根。

**★ 母线电压安全窗口**（TI DRV8313 手册 SLVSBA5D）：芯片 Rec. VM 8–60V / ABS MAX 65V / UVLO 6.3–8V / OCP 3A / 峰值 2.5A-RMS 1.75A；**芯片无 OVP**，只有 OCP+UVLO+TSD。板级口径（MKS V3.2）12–30V → **"板 ≠ 芯片"**（电容耐压与 LM7808 功耗先到限）。
**★ `voltage_power_supply` 是量纲不是注释**：内部 `duty = U / voltage_power_supply`，实际输出 = duty × 真实母线。只拧电源不改固件 → 实际电压静默放大 `Vbus/voltage_power_supply` 倍。铁律：两个数字必须一致，改完重跑体检。
**★ 过压先死电机不是驱动**：R=21.2Ω，铜损 `P≈1.5·V²/R`（限压 2V=0.28W / 12V=10.2W / 24V=40.8W）；24V 下 1.13A 仍在驱动能力内。实操：母线固定 12V 且与固件一致，**CC 0.3–0.5A 当第一道保护**，要"更有劲"调 `voltage_limit` 而非拧电源。详见 `firmware-pio/调试台与整定矩阵.md` §十。

## 二、代码与工具链（vendor 库模式，全部编译验证过）

`firmware-pio/src/`：main.cpp(开环) / selftest.cpp(自检) / closedloop.cpp(AS5600 闭环 FOC) / gimbal_tracker.cpp(视觉追踪) / knob.cpp(智能旋钮) / as5600test.cpp(寄存器诊断)。
`vision/vision_tracker.py`：PC OpenCV 追踪 → 串口 `T<弧度>\n`。
`firmware-pio/tools/serial_bench.py`：**整定调试台**，自动跑序列→采遥测→算指标→出 CSV+SVG，零额外依赖。子命令 `step/velstep/hold/repro/raw/check/selftest`；`selftest` 无需硬件即验证指标算法本身。`--set` 下发任意固件命令使整定矩阵可脚本化。
`firmware-pio/tools/screen_step.py`：多组参数各跑 N 次出对比表（应对本机双稳态）。
`firmware-pio/tools/serial_capture.py`：串口捕获，支持等新 COM 口、`--send`。
**整定手册**：`firmware-pio/调试台与整定矩阵.md`（E1→E6 六步，OFAT 一次只改一个参数，§十=电压/§十一=双稳态方法论）。

**约定**：SimpleFOC 锁 2.3.4（2.4.0 的 `enableTimerClock` 签名与 framework-arduinoststm32 4.30000.0 不兼容）；U8g2 锁 2.36.18；platformio.ini 用 `build_src_filter` 多环境切换。F407 Wire：PB7=SDA / PB6=SCL（**与 UNO 相反**）。IntelliSense 勿设 `C_Cpp.default.compileCommands`（会截胡），用 `gen_intellisense.py` 生成 includePath。

**★ printf 浮点必须加 `-Wl,-u,_printf_float`**：newlib-nano 默认裁掉 %f，snprintf 输出浮点为空（症状：OLED 只显示单位没数字）。**不能写成 `-u _printf_float`** —— PIO 会拆成孤立 `-u` 吞掉 `-mcpu`，CMSIS 报 "Unknown Arm architecture profile"。

**★ SimpleFOC Sensor 是 pull 模型**：getter 返回缓存，必须有人调 `sensor.update()` 才真读芯片。含 `loopFOC()` 的固件由它自动 pull；**不含 loopFOC 的固件（如 knob）必须自己每帧调 `sensor.update()`**，否则读数恒为上电初值。API 区别：`getAngle()`=多圈累计 / `getMechanicalAngle()`=单圈 0~2π wrap / `getSensorAngle()`=原始角。增量式读法须用浮点累加器攒步进，取整只在显示层。

**★★ 不要重复调用 `motor.shaftAngle()` / `shaftVelocity()`**：二者内部会推进 `LPF_angle`/`LPF_velocity` 状态——多调一次等于多滤一次，既改变控制环行为、又使显示值与控制器实际用值不一致。正确做法：读 public 缓存成员 **`motor.shaft_angle` / `motor.shaft_velocity`**（`move()` 每周期刷新）。`closedloop.cpp` 2026-09-12 修了 3 处。

**★ 周期性串口输出必须非阻塞**：USB CDC 的 `write()` 在主机不读、缓冲满时阻塞（与 OLED 全帧刷新同类问题）。规范：**单缓冲一次 write + 先查 `Serial.availableForWrite()`，缓冲不够就丢样本并计数**（丢样本可接受，阻塞不可接受）。不要用内置 `motor.monitor()`（分多次 print，库源码自注 "significantly slowing the execution down"）。遥测默认关闭。

**★ OLED 与电机控制循环冲突**：全帧 `sendBuffer` 1KB 阻塞 ~25–30ms，10Hz 定时刷新 = 每秒冻结 10 次 = 电机顿挫。**任何含 `move()`/`loopFOC()` 的主循环禁止周期性全帧刷新**。解法：main.cpp 事件驱动（参数变才画）；closedloop.cpp 两段式（静态区事件驱动 + 动态区 `updateDisplayArea` 局部发 256B/4Hz）。

## 三、串口 / 烧录 / 诊断

- `Serial` = **USB CDC**，走 PA11/PA12 板载 USB 口。**ST-Link 不提供串口** → 不插 USB 线就没有 COM 口。烧录走 SWD，打印走 USB，互不冲突。
- **★★ USB CDC 必须 `ser.setDTR(True)`**：DTR 为低时固件不发数据（COM 口正常枚举、TX 成功、收到 0 字节）。VID:PID = 0483:5740。CDC 波特率虚设，零字节不要怀疑波特率。
- **★ 12V 开着烧录必失败** —— PWM 开关噪声毁 SWD（"Failed to write memory" / verify 随机错位），烧录前必须关电源输出。OpenOCD 反复失败时换 CubeProgrammer CLI：`STM32_Programmer_CLI.exe -c port=SWD -d firmware.elf -v -rst`（官方驱动栈，实测可靠）。远程复位 `-c port=SWD -rst` 可配合后台 serial_capture 抓启动横幅。
- **无串口时的验证通道**：`selftest.cpp` 上电自动跑 scanI2C→testOLED→testLED，看 PC13 LED 闪 5 下 + OLED 显示 `I2C found: N`（N=2 正常）。实测已确认：0x36 AS5600 + 0x3C OLED。

**★★ "角度恒为 0" 根因（源码级已解决）**：12V 未通电 → `alignSensor()` 返回 0 → `sensor_direction` 保持 `UNKNOWN(=0)` → `shaftAngle() = 0 × getAngle() = 0`；且 `initFOC()` 失败分支会 `disable()`。此症状与"编码器损坏"完全一致 → **必须看原始角 raw**。因 `loopFOC()` 第一行就 `sensor->update()` 且先于 `enabled` 判断，**无 12V 也能验编码器**。固件已加 `Z` 命令 + 上电自动体检 `printDiag()`。**上机第一步永远先跑 `check` 子命令**（力矩模式 + `T0` 放手轮，手转看 raw 跨度 >5° 即正常）。
遥测 `D` 行 7 段：`D,<ms>,<mode>,<tgt>,<ang>,<vel>,<raw>`（旧 6 段仍兼容）。开启命令 `O<ms>`（默认关，最小 5ms）；`A<数字>`=位置环 P，`V<数字>`=速度上限。

## 四、★★ 控制环：双稳态与定稿（2026-09-12 E6 复现测试确立）

**现行唯一权威配置**（已写入 `src/closedloop.cpp` 默认值并烧录）：
```
L = 2V    V = 3 rad/s    P_velocity = 0.5    I_velocity = 10
F = 0.05 (LPF Tf)        A_angle = 10
```
90° 阶跃实测（每组 6 次复现全过）：上升 ~450ms / 超调 ~0.1% / 调节 ~707ms / 纹波峰峰 ≤0.17°。
裕量：`A=10` 对边界 13~16（≈2×）；`F=0.05` 对边界 0.03。

**★ 真凶 = 位置环 `A`（旧默认 20）**：A=20 实测四跑四振、六跑六振 —— 稳态 ±9.6° 摆动、~18.85 Hz、速度峰峰 10.7 rad/s（远超 V=3）、不衰减 = **自持极限环**。机制：位置环把速度反馈噪声放大成极限环（低速时 12bit 编码器量化 0.088° 被微分放大成 ±0.5 rad/s 抖动）→ 降 A 减放大倍数、加 F 削噪声源，**两个旋钮都有效**。
**已证伪**：①"A/F 不是杠杆、真杠杆是 I" —— 复测证明 A 才是主杠杆；②"速度环饱和 → 要 V≤4" —— A 仍 20 时单独降到 V=2 仍四跑三振。`V=3` 只决定阶跃斜率，**非"防饱和"**。

**★★ 方法论铁律（本机最关键的一条）**：**本机是双稳态** → 同参数会在「干净衰减」与「自持 ~19Hz 极限环」之间跳。因此**任何"稳定/不稳定"结论必须由 ≥6 次重复的 min/max 支撑，单次漂亮的波形 = 侥幸**。
- **△ 机械边界条件是调参前置条件**：定子必须固定（夹/胶/扎带），转子必须自由。定子未固定时数据全是扭簧-质量伪影（"超调 0.1%/上升 160ms"是假象，因为定子反扭吸收了振荡）。**稳态指标两种情况下都可信，瞬态指标不可信**。
- **△ 测试序列不能污染初始条件**：`hold` 后电机已在第 N 圈，`step` 若用绝对角 0 当起点会倒转几十圈 → 误判失控。已修复：`step` 改为相对当前位置阶跃。
- **△ 排障先判别哪个环在振荡**（速度模式下 A 不参与 → 排除位置环），再找杠杆。
- **△ 工具安全铁律**：凡"会改设备参数"的工具，**退出必须回到已知安全态**。`--set` 改的是固件 RAM，退出不还原 → 曾把失稳增益 `P=2.0` 留在固件里导致剧烈振荡。现 `SAFE_PARK = ["P0.5","I10","F0.03","A10"]`，任何退出路径先恢复再发 `O0`。**凡能改电机动态的参数都必须列入 SAFE_PARK**。
- **△ 判据四项**：L1 曾因不测纹波把极限环判成"三项全过可以写简历" → 已增设**纹波绝对判据**（峰峰 max ≤1°，**不适用 CV**）。
- **失控急救**：远程复位 `STM32_Programmer_CLI -c port=SWD -rst` → 固件重启后进速度模式+目标 0（已验证稳定），最可靠的停车方式。
- 待办：用定稿固件重跑 E6（`repro` 10×90°）。⚠️ 上次是在 **12V 关闭**下烧录复位的 → **开 12V 后必须再复位一次**（FOC 带电重新对齐）。

**★ 度量纪律**：**不要用相邻遥测样本差分算"瞬时速度"**（量化 0.088°/LSB + 间隔抖动 5~12ms 会放大数倍）。可信的只有①角度轨迹 ②固件侧指标。静止时有 ∼50Hz 的 ±1 LSB 量化极限环属固有现象，靠 `F` 抑制。

## 五、沙箱坑（本机开发环境）

- pio 全路径 `/c/Users/16689/.platformio/penv/Scripts/pio.exe`
- **编译报 `SHFileOperationW 失败: 0x2` 的正解**：`rm -rf .pio/build/<env>`（只删单个环境目录）+ `dangerouslyDisableSandbox: true` + 前台运行
- **★ platformio.ini 禁用 `build_cache_dir`**：SCons CacheDir 要创建+删除自己的锁文件，沙箱删除保护拦截 → 所有环境编译开始前直接 FAILED（错误栈在 `env.CacheDir`）。跨环境编译缓存与本机沙箱不兼容
- 沙箱 SAFE_DELETE 拦截 `.platformio` 锁文件批量删除 → pio 装库失败（仅噪音，不影响编译）
- PowerShell 的 `Remove-Item -Recurse -Force` **静默失败**，必须用 Bash `rm -rf`
- 全量重编 ~150s → timeout ≥300000；后台任务不获越权批准
- 过滤噪音：`pio run 2>&1 | grep -v "safe-delete" | tail -30`
- **Git Bash 的 `/d/xxx` 路径不能直接传给 Windows python**（会变成 `d:\d\xxx`，ENOENT）。先 `cd` 用相对路径，或传 `D:/xxx`

## 六、学习文档双轨制（2026-09-12 确立，求职期主力）

- **`底层经典问题集.md` = 社招面试题库**（93 题 / 12 模块），按"面试官会怎么问+怎么追问"组织。模块十 Q70~Q83 = 双核简历模板与话术；模块十一 Q84~Q93 = 前沿趋势（AI 下沉 MCU / EU CRA 2027 / RISC-V / RTOS 生态集中化）+ 趋势速记卡（8 主题可报数字）。冲刺表 18 天。
- **`底层工程实践问题集.md` = 工程实战题库**（49 题，就业后用）。《底层学习路线.md》的 9 层"配套练习"链接指向此文件。
- **面试素材铁律**：用户亲历事故是最好的社招答案（烧录噪声毁 SWD / OLED 阻塞顿挫 / 量化噪声极限环）。新事故按"症状→假设→证伪→根因→对策→沉淀"存档。
- **★ 涉及"某芯片有无某特性"必须查对应 RM/TRM**，社区共识常错。已核实：F4 **有**位带（RM0090 §2.3.3，外设+SRAM 都映射），无位带的是 M7(F7/H7) 与 M0/M0+；中断延迟进入≤12/尾链 6（**返回官方有 10/12 两种表述** → 面试说"约 12+尾链 6"）；F4 **无**硬件 ADC 过采样（F3/L4/F7/H7 有）；**CCM RAM 不能被 DMA 访问**。
- **`tools/reflow_interview_bank.py` = 题库排版脚本**（2026-10-05）：`底层经典问题集.md` 的卡片式排版由它自动产出，并重建顶部索引区（元信息表 + 模块目录 + ★★★ 速查表 67 题 + ★★ 补充表 26 题）。每次运行自动备份到 `archive/`。**改题库内容时行首格式必须保持**：得分点 `①`、追问 `→`、加分/减分 `**★加分`，否则解析漏内容；**题号不补零**（正文有大量"见 Q59"式交叉引用）。旧排版留档 `archive/底层经典问题集-排版v1-*.md`。

## 七、用户在职项目（简历核心资产，须脱敏）

**双核嵌入式系统**：通信核跑 RTOS（任务/协议/参数管理）+ 功率核裸机（用户称 **EOS**），承担 xx kHz 硬实时控制与保护。
**面试价值：双核经历是差异化优势**（多数候选人为单核背景）。简历可写但必须脱敏（不谈产品/客户/内部代号/参数/代码）。待补：多核启动 / IPC / Cache 一致性 / 跨核互斥 / 实时性度量。

## 八、规划与路线

**P1 单轴视觉追踪（PC+摄像头） → P2 两轴（第2个 C2208+支架） → P3 OpenMV/K210 边缘化视觉。**
`学习路线图.md`：6 阶段（控制理论补课→P1跑通→FOC深化→ROS2+Linux副技能→LeRobot具身智能→两轴+边缘化），每周 10–15h。已核实资源：江协PID BV1G9zdYQEr3、灯哥开源SimpleFOC、鱼香ROS BV19U4y1n7CQ、赵虚左 BV1VB4y137ys、DR_CAN space 230105574。纪律：动手:看视频 ≥1:1，每阶段产出可见物。
**电源**（2026-09-04 反接事故后修正）：12V 适配器方案作废，改用**带限流 CC 的可调直流电源 3-24V / 0-5A**，靠预设限流防再次烧毁；纯调电压无 CC 的便宜货无保护价值。
