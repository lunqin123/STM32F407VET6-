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
void doMode  (char *cmd) {
    int m = cmd ? atoi(cmd) : 1;
    if      (m == 0) motor.controller = MotionControlType::torque;
    else if (m == 2) motor.controller = MotionControlType::angle;
    else             motor.controller = MotionControlType::velocity;
    Serial.print("mode = ");
    Serial.println(m == 0 ? "torque(力矩)" : (m == 2 ? "angle(位置)" : "velocity(速度)"));
}

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
    snprintf(buf, sizeof(buf), "Ang %6.1f deg", motor.shaftAngle() * 180.0f / PI);
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

    /* 控制参数（闭环默认速度环） */
    motor.voltage_limit  = VOLTAGE_LIMIT;
    motor.velocity_limit = 10.0f;     // rad/s，位置环也受此上限约束
    // 速度环 PID（云台电机典型起点，必要时再调）
    motor.PID_velocity.P = 0.2f;
    motor.PID_velocity.I = 20.0f;
    motor.PID_velocity.D = 0.0f;
    motor.LPF_velocity.Tf = 0.03f;    // 速度低通滤波（低速量化噪声主滤波器，F 命令可调）
    // 位置环 P（位置环内部再套一层速度环）
    motor.P_angle.P = 20.0f;
    motor.controller = MotionControlType::velocity;

    motor.init();
    motor.initFOC();                  // ★ 电角度对齐：上电会轻微动一下，正常

    command.add('T', doTarget, "target (rad/s 或 rad)");
    command.add('L', doLimit,  "voltage limit (V)");
    command.add('M', doMode,   "mode 0=trq 1=vel 2=pos");
    command.add('F', doTf,     "velocity LPF Tf (0.01-0.1)");
    command.add('P', doP,      "vel PID P");
    command.add('I', doI,      "vel PID I");

    Serial.println("就绪。T<速度> M<模式> L<限压> F<滤波> P/I<PID>");
}

/* ---------- 6. 主循环 ---------- */
void loop()
{
    motor.loopFOC();          // FOC 调制 + 读编码器（闭环核心，必须每帧调）
    motor.move();              // 执行运动控制（用 motor.target）
    command.run();            // 处理串口命令

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
    snprintf(abuf, sizeof(abuf), "Ang %6.1f deg", motor.shaftAngle() * 180.0f / PI);
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
