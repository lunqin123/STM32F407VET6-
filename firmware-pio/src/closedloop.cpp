/* ============================================================
 * STM32F407VET6 + SimpleFOC + AS5600 + SSD1306 —— 闭环 FOC（速度/位置环）
 *
 * 【这是什么】
 *   开环（main.cpp）只能"硬拖"电机转，不知道转子在哪、转多快。
 *   闭环 = 用 AS5600 磁编码器实时读转子角度，FOC 算法据此做：
 *     - 速度环：给定目标转速，自动维持（PID 调三相电压）
 *     - 位置环：给定目标角度，自动转到并"锁住"（机械臂关节的核心）
 *   这才是伺服/关节控制的核心，也是简历上值钱的部分。
 *
 * 【硬件前提】电机套装自带 AS5600（I2C，已预装磁铁），接上即闭环就绪。
 *   AS5600 与 OLED 共用 I2C1（PB6/SCL, PB7/SDA）：
 *     地址 AS5600=0x36、OLED=0x3C，不冲突，不用开第二条总线。
 *
 * 【引脚】PWM=PA8/9/10(TIM1_CH1/2/3)，EN=PB0，I2C=PB7(SDA)/PB6(SCL)，LED=PC13
 *
 * 【串口命令】
 *   T<数字>  设目标（速度环=rad/s；位置环=弧度，如 T3.14 转到半圈）
 *   M<0/1/2> 切模式：0=力矩 1=速度（默认） 2=位置
 *   L<数字>  调电压上限（起步 2V，确认不抖再调大）
 *   F<数字>  速度低通 Tf（低速量化噪声主滤波器）
 *   P/I<数字> 速度环 PID    A<数字> 位置环 P    V<数字> 速度上限(rad/s)
 *   Z        体检快照：I2C 扫描 + 对齐状态 + 原始编码器角（排查"角度恒 0"）
 *   O<毫秒>  遥测周期（0=关闭，**默认关闭**）。开启后每周期输出一行：
 *              D,<ms>,<mode>,<target_rad>,<angle_rad>,<velocity_rad_s>,<raw_rad>
 *            ★ 末段 <raw_rad> = 编码器**未经方向换算**的原始累计角。对齐失败时
 *              motor.shaft_angle 会恒为 0（sensor_direction 仍为 UNKNOWN，乘完还是 0），
 *              但 raw_rad 照常跟随真实轴位置变化 —— 它是判断"编码器到底有没有在
 *              读数"的唯一可靠字段：用手转轴，raw 变 = 编码器好。
 *            供 tools/serial_bench.py 自动采数。最小 5ms（200Hz）。
 *
 * 【为什么遥测默认关闭】本文件是闭环控制固件，任何周期性串口输出都在
 *   抢主循环时间。Serial 走 USB CDC，若主机不读、发送缓冲满，write 会阻塞
 *   ——和"OLED 全帧刷新导致顿挫"是同一类问题。故：默认关闭；开启后仍用
 *   availableForWrite() 兜底，缓冲不够就丢样本（丢样本可接受，阻塞不可接受）。
 *
 * 【安全】VOLTAGE_LIMIT 起步 2V；initFOC() 上电会轻微动一下做电角度对齐，正常。
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <SimpleFOC.h>
#include <U8g2lib.h>
#include <Screen.h>

/* ---------- 1. 硬件参数区 ---------- */
#define POLE_PAIRS     7        // C2208-100T = 7 极对（与 main.cpp 一致）
#define PIN_UH         PA8      // U 相 → TIM1_CH1
#define PIN_VH         PA9      // V 相 → TIM1_CH2
#define PIN_WH         PA10     // W 相 → TIM1_CH3
#define PIN_EN         PB0      // 驱动板使能（MKS Shield 必需，拉高才输出）
#define LED_PIN        PC13
#define OLED_SDA       PB7      // ★ F407 的 Wire 默认 SDA=PB7（非 UNO 习惯）
#define OLED_SCL       PB6

#define SUPPLY_VOLTAGE 12.0f
#define VOLTAGE_LIMIT  2.0f     // ★ 起步限压，防过流；用 L 命令调大
/* OLED 刷新由 lib/Screen 公共层接管（两段式 + 息屏），本文件不再直接操作 U8g2 */

/* ---------- 2. 对象 ---------- */
BLDCMotor        motor  = BLDCMotor(POLE_PAIRS);
BLDCDriver3PWM   driver = BLDCDriver3PWM(PIN_UH, PIN_VH, PIN_WH, PIN_EN);
// AS5600：I2C 默认地址 0x36，12bit（SimpleFOC 的 AS5600_I2C 宏已含这些参数）
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);

bool oled_ok = false;

/* ---------- 3. 串口命令 ---------- */
Commander command = Commander(Serial);
void doTarget(char *cmd) { command.scalar(&motor.target, cmd); }
void doLimit (char *cmd) { command.scalar(&motor.voltage_limit, cmd); }
void doTf    (char *cmd) { command.scalar(&motor.LPF_velocity.Tf, cmd); }   // F：速度滤波
void doP     (char *cmd) { command.scalar(&motor.PID_velocity.P, cmd); }   // P：速度环比例
void doI     (char *cmd) { command.scalar(&motor.PID_velocity.I, cmd); }   // I：速度环积分
void doAp    (char *cmd) { command.scalar(&motor.P_angle.P, cmd); }        // A：位置环 P（在线整定必需）
void doVlim  (char *cmd) { command.scalar(&motor.velocity_limit, cmd); }   // V：速度上限（位置环斜率）
void doMode  (char *cmd) {
    int m = cmd ? atoi(cmd) : 1;
    if      (m == 0) motor.controller = MotionControlType::torque;
    else if (m == 2) motor.controller = MotionControlType::angle;
    else             motor.controller = MotionControlType::velocity;
    Serial.print("mode = ");
    Serial.println(m == 0 ? "torque(力矩)" : (m == 2 ? "angle(位置)" : "velocity(速度)"));
}

/* ---------- 3b. 遥测（给上位机自动采数用；默认关闭） ---------- */
static uint32_t tel_ms      = 0;   // 0 = 关闭；>0 = 周期(ms)
static uint32_t tel_last    = 0;
static uint32_t tel_sent    = 0;
static uint32_t tel_dropped = 0;

void doTel(char *cmd) {
    long v = cmd ? atol(cmd) : 0;
    if (v < 0) v = 0;
    if (v > 0 && v < 5) {                       // 1~4ms 会把主循环拖慢，强制抬到 5ms
        Serial.println("warn: 最小 5ms(200Hz)，已抬到 5ms");
        v = 5;
    }
    tel_ms      = (uint32_t)v;
    tel_last    = millis();
    tel_sent    = 0;
    tel_dropped = 0;
    Serial.print("telemetry = ");
    Serial.print(tel_ms);
    Serial.println(tel_ms ? " ms ON (D,ms,mode,tgt,ang,vel)" : " OFF");
}

/* ---------- 3c. 体检：把"为什么角度读不到"直接打到串口（避免瞎猜） ----------
 * 背景（真踩过的坑）：FOC 对齐失败时 motor.shaft_angle 会**恒为 0**，症状和
 * "编码器坏了"一模一样，肉眼无法区分。根因链：
 *   alignSensor() 没测到转动 → 返回 0 → sensor_direction 仍 UNKNOWN(=0)
 *   → shaftAngle() = 0 × sensor->getAngle() = 0（永远 0）
 *   → 且 initFOC() 在失败分支里调了 disable()，驱动被关，电机也不会转
 * 所以必须把"对齐状态"和"原始编码器角"分开暴露出来看。 */
static bool focAligned() {
    return (motor.sensor_direction != Direction::UNKNOWN) && _isset(motor.zero_electric_angle);
}

static const char *statusName(uint8_t s) {
    switch (s) {
        case 0x00: return "uninitialized";
        case 0x01: return "initializing";
        case 0x02: return "uncalibrated(仅开环可用)";
        case 0x03: return "calibrating";
        case 0x04: return "ready(闭环可用)";
        case 0x08: return "error";
        case 0x0E: return "CALIB_FAILED";
        case 0x0F: return "INIT_FAILED";
        default:   return "?";
    }
}

static uint8_t scanI2C() {
    uint8_t n = 0;
    Serial.print("I2C 扫描:");
    for (uint8_t a = 1; a < 127; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) {
            Serial.print(" 0x"); Serial.print(a, HEX);
            n++;
        }
    }
    if (!n) Serial.print(" 无设备（查 PB6/PB7 接线与上拉）");
    Serial.println();
    return n;
}

/* 打印一次完整体检快照（上电自动调一次，之后可用 Z 命令随时复查） */
static void printDiag() {
    scanI2C();
    Serial.print("sensor_direction  = "); Serial.print((int)motor.sensor_direction);
    Serial.println(motor.sensor_direction == Direction::UNKNOWN ? "  (UNKNOWN: 未对齐)"
                 : motor.sensor_direction == Direction::CW  ? "  (CW)"
                                                            : "  (CCW)");
    Serial.print("zero_electric_ang = ");
    if (_isset(motor.zero_electric_angle)) Serial.println(motor.zero_electric_angle, 4);
    else                                   Serial.println("NOT_SET");
    Serial.print("motor_status      = 0x"); Serial.print((int)motor.motor_status, HEX);
    Serial.print("  "); Serial.println(statusName((uint8_t)motor.motor_status));
    Serial.print("driver enabled    = "); Serial.println(motor.enabled);
    Serial.print("raw sensor angle  = "); Serial.println(sensor.getAngle(), 4);
    Serial.print("shaft_angle       = "); Serial.println(motor.shaft_angle, 4);
    if (focAligned()) {
        Serial.println(">>> 体检通过：FOC 已对齐，闭环就绪");
    } else {
        Serial.println(">>> 体检未通过：FOC 未对齐 → shaft_angle 会恒为 0。");
        Serial.println("    最常见原因：12V 未通电（电机无法被拉到电角度 0）→ 通电后按 RESET。");
        Serial.println("    想在没有 12V 时验证编码器：用手缓慢转轴，看遥测 raw 字段是否跟着变。");
    }
}
void doStatus(char *) { printDiag(); }

/* ---------- 4. 屏幕绘制（统一走 lib/Screen 公共层，本文件只写绘制回调） ---------- */
/* 带划分：tile 行 0-1 / 2-3 / 4-5 / 6-7（每带 16px，文字基线 12/28/44/60） */
static void drawTitle(U8G2 &u)   // 静态：标题
{
    u.drawStr(0, 12, "F407 FOC closed");
    u.drawHLine(0, 14, 128);
}
static void drawParam(U8G2 &u)   // 静态：模式 + 目标
{
    char buf[24];
    const char *mode = (motor.controller == MotionControlType::angle)  ? "POS"
                      : (motor.controller == MotionControlType::torque) ? "TRQ" : "VEL";
    snprintf(buf, sizeof(buf), "%s  T %.2f", mode, motor.target);
    u.drawStr(0, 28, buf);
}
static void drawAux(U8G2 &u)     // 静态：转速/圈数换算 + 限压
{
    char buf[24];
    if (motor.controller == MotionControlType::angle)
        snprintf(buf, sizeof(buf), "%5.2f rev  U%.1fV", motor.target / (2.0f * PI), motor.voltage_limit);
    else
        snprintf(buf, sizeof(buf), "%5.1f rpm  U%.1fV", motor.target * 60.0f / (2.0f * PI), motor.voltage_limit);
    u.drawStr(0, 44, buf);
}
static void drawAngle(U8G2 &u)   // 动态：实测角度（AS5600 真值，局部重发）
{
    char buf[24];
    /* ★ 读缓存成员，不要调 shaftAngle()（会推进 LPF_angle 状态） */
    snprintf(buf, sizeof(buf), "Ang %6.1f deg", motor.shaft_angle * 180.0f / PI);
    u.drawStr(0, 60, buf);
}

/* ---------- 5. 初始化 ---------- */
void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);

    Serial.begin(115200);
    delay(1500);

    Serial.println("\n===== STM32F407VET6 · SimpleFOC 闭环 (AS5600) =====");

    /* I2C：先 setSDA/SCL 再 begin；AS5600 与 OLED 同总线，均支持 400kHz */
    Wire.setSDA(OLED_SDA);
    Wire.setSCL(OLED_SCL);
    Wire.begin();
    Wire.setClock(400000);

    oled_ok = oled::begin();
    Serial.println(oled_ok ? "OLED OK" : "OLED 未找到（不影响闭环）");
    if (oled_ok) {
        oled::addBand(0, 2, false, drawTitle);   // 静态：标题
        oled::addBand(2, 2, false, drawParam);   // 静态：模式+目标
        oled::addBand(4, 2, false, drawAux);     // 静态：换算+限压
        oled::addBand(6, 2, true,  drawAngle);   // 动态：实测角度（局部重发）
        oled::setSleepTimeout(30000);            // 30s 无角度变化/命令自动息屏
    }

    /* 编码器 */
    sensor.init();
    Serial.println("AS5600 初始化完成（I2C 0x36）");

    /* 驱动板 */
    driver.voltage_power_supply = SUPPLY_VOLTAGE;
    driver.init();
    driver.enable();                  // 拉高 EN，驱动级才输出三相
    motor.linkDriver(&driver);
    motor.linkSensor (&sensor);       // 闭环关键：把编码器交给电机对象

    /* ★★ 控制参数（闭环默认速度环）
     *   下面这组数字是 **2026-09-12 上机整定的定稿值**，不是"随手写的典型起点"。
     *   来历见《调试台与整定矩阵.md》§九（F×I / P 扫描表）、§十（电压窗口）、
     *   §十一（~19Hz 自持极限环与真凶 A）。
     *
     *   为什么必须写进默认值：整定值原本只靠串口 --set 写在固件 **RAM** 里，
     *   一复位就回到默认 —— 而**旧默认（I=20 / A=20）在本机实测都是失稳点**。
     *   也就是说：复位 = 电机重新变成不稳定状态。
     *
     *   · I = 10   ← 旧默认 20 实测失稳；I 是速度环的**真杠杆**，不是 P 也不是 F。
     *   · P = 0.5  ← 本机最优；P ≥ 1.0 重新失稳，P = 0.2 增益不足（调节时间拖长）。
     *   · F = 0.05 ← ★ E6 复现测试后由 0.03 上调。速度反馈在低速被 12bit 编码器
     *                量化噪声主导（微分把 1 LSB 放大成 ±0.5 rad/s 抖动）；F 太弱时
     *                位置环会把这层噪声放大成 ~19 Hz、±10° 的**自持极限环**。
     *   · A = 10   ← ★ 位置环 P，**本次真正的真凶**（旧默认 20）。
     *                实测（每组 4~6 次、L=2V、V=3）：A20 → 纹波 15~22°、四跑四振；
     *                A16 → 双稳态（有时 0.09° 有时 21°）；边界在 **A ≈ 13~16**；
     *                A13/A10/A8 → 全部干净。选 A=10，取 ~2× 裕量。
     *   · V = 3    ← 位置环的速度上限（限斜率）。曾经被误判为振荡主因（"速度环饱和"
     *                假说），复测**证伪**：单独把 V 降到 2 仍四跑三振。
     *                保留 3 是因为它给出合适的 90° 阶跃斜率（上升 ~450ms），
     *                不是因为"防饱和"。
     *
     *   ★ 定稿组合（2026-09-12，每组 6 次复现全部通过）：
     *        L=2V  V=3  P=0.5  I=10  F=0.05  A=10
     *     90° 阶跃：上升 ~450ms / 超调 ~0.1% / 调节 ~707ms / 纹波峰峰 ≤0.17°
     *     两个旋钮都留 ~2× 裕量（A: 10 vs 边界 13~16；F: 0.05 vs 边界 0.03）。
     *   → 想改这些数：**必须**用 tools/screen_step.py 跑 ≥6 次看 min/max 离散度。
     *     本机在旧工作点附近是双稳态的，**单次跑出来的漂亮数字等于侥幸**（已实测
     *     同参数一次 0.0° 一次 21°）。 */
    motor.voltage_limit  = VOLTAGE_LIMIT;
    motor.velocity_limit = 3.0f;      // rad/s，位置环速度上限（限斜率，非"防饱和"）
    motor.PID_velocity.P = 0.5f;      // 速度环 P（定稿 0.5）
    motor.PID_velocity.I = 10.0f;     // 速度环 I（定稿 10 —— 20 会失稳，不要改回去）
    motor.PID_velocity.D = 0.0f;
    motor.LPF_velocity.Tf = 0.05f;    // 速度低通滤波（低速量化噪声主滤波器，F 命令可调）
    // 位置环 P（位置环内部再套一层速度环）
    motor.P_angle.P = 10.0f;          // 定稿 10（20 会激起 ~19Hz 自持极限环）
    motor.controller = MotionControlType::velocity;

    motor.init();
    /* ★ 上电自动对齐：会给一相通电把转子拉到电角度 0，电机会轻微动一下，正常。
     *   若此刻 12V 没通，电机"拉不动"→ 对齐失败 → 之后角度读数恒为 0。
     *   这不是固件 bug，是没动力；通电后按 RESET 即可重新对齐。 */
    motor.initFOC();

    Serial.println("--- 启动体检 ---");
    printDiag();                      // 角度读不到时，先看这里，别猜

    command.add('T', doTarget, "target (rad/s 或 rad)");
    command.add('L', doLimit,  "voltage limit (V)");
    command.add('M', doMode,   "mode 0=trq 1=vel 2=pos");
    command.add('F', doTf,     "velocity LPF Tf (0.01-0.1)");
    command.add('P', doP,      "vel PID P");
    command.add('I', doI,      "vel PID I");
    command.add('A', doAp,     "angle PID P");
    command.add('V', doVlim,   "velocity limit rad/s");
    command.add('Z', doStatus, "diagnostic snapshot");
    command.add('O', doTel,    "telemetry period ms (0=off, >=5)");

    Serial.println("就绪。T<目标> M<模式> L<限压> F<滤波> P/I<速度环PID> A<位置环P> V<速度上限> Z<体检> O<遥测ms>");
}

/* ---------- 6. 主循环 ---------- */
void loop()
{
    motor.loopFOC();          // FOC 调制 + 读编码器（闭环核心，必须每帧调）
    motor.move();              // 执行运动控制（用 motor.target）
    command.run();            // 处理串口命令

    /* 遥测（默认关闭，见文件头说明）
     * ① 单缓冲一次 write —— 不用 SimpleFOC 内置 motor.monitor()，因为它按变量
     *    分多次 print，一行要十几次小写，USB CDC 开销更大（库源码自己标注
     *    "significantly slowing the execution down"）。
     * ② ★ 读 motor.shaft_angle / shaft_velocity 这两个**缓存成员**，
     *    不要再调 motor.shaftAngle() / shaftVelocity()：后者内部会推进
     *    LPF_angle / LPF_velocity 滤波器状态，每循环多调一次就等于多滤一次，
     *    既改变控制环行为、又让数据与控制器实际用值不一致。
     *    （move() 每周期已刷新这两个成员，读它们零副作用。）
     * ③ 先查 availableForWrite()：缓冲不够就丢样本并计数，绝不阻塞控制环。 */
    if (tel_ms) {
        uint32_t now = millis();
        if (now - tel_last >= tel_ms) {
            tel_last = now;
            static char tb[96];
            /* 第 7 段 raw = 编码器原始累计角（未乘 sensor_direction）：
             * 对齐失败时 shaft_angle 恒 0，但 raw 仍随真实轴位置变化 —— 靠它
             * 才能把"编码器坏"和"没通电没对齐"两件事分开。 */
            int n = snprintf(tb, sizeof(tb), "D,%lu,%d,%.4f,%.4f,%.4f,%.4f\n",
                             (unsigned long)now, (int)motor.controller,
                             motor.target, motor.shaft_angle, motor.shaft_velocity,
                             sensor.getAngle());
            if (n > 0 && Serial.availableForWrite() >= n) {
                Serial.write((const uint8_t *)tb, (size_t)n);
                tel_sent++;
            } else {
                tel_dropped++;
            }
        }
    }

    /* OLED（lib/Screen 公共层）：参数变化标脏静态带；角度变了标脏动态带。
     * Screen 内部负责事件驱动全帧 + tile 局部重发 + 息屏/兜底，循环零阻塞设计 */
    static float   last_tgt  = -1e9f;
    static float   last_ulim = -1e9f;
    static uint8_t last_mode = 255;
    static char    last_ang[24] = "";
    if (motor.target != last_tgt || motor.voltage_limit != last_ulim
        || (uint8_t)motor.controller != last_mode) {
        oled::invalidate(1);
        oled::invalidate(2);
        last_tgt  = motor.target;
        last_ulim = motor.voltage_limit;
        last_mode = (uint8_t)motor.controller;
    }
    char abuf[24];
    /* ★ 同样读缓存成员 motor.shaft_angle，不要调 motor.shaftAngle()：
     * 后者会推进 LPF_angle 滤波器状态（当前 LPF_angle.Tf=0 无害，
     * 但一旦启用角度滤波，每循环多调一次就会改变控制行为） */
    snprintf(abuf, sizeof(abuf), "Ang %6.1f deg", motor.shaft_angle * 180.0f / PI);
    if (strcmp(abuf, last_ang) != 0) {
        oled::invalidate(3);
        strcpy(last_ang, abuf);
    }
    oled::tick();

    static uint32_t last_led = 0;
    if (millis() - last_led > 500) {
        last_led = millis();
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
}
