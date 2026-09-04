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
#define OLED_REFRESH_MS 100

/* ---------- 2. 对象 ---------- */
BLDCMotor        motor  = BLDCMotor(POLE_PAIRS);
BLDCDriver3PWM   driver = BLDCDriver3PWM(PIN_UH, PIN_VH, PIN_WH, PIN_EN);
// AS5600：I2C 默认地址 0x36，12bit（SimpleFOC 的 AS5600_I2C 宏已含这些参数）
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

bool oled_ok = false;

/* ---------- 3. 串口命令 ---------- */
Commander command = Commander(Serial);
void doTarget(char *cmd) { command.scalar(&motor.target, cmd); }
void doLimit (char *cmd) { command.scalar(&motor.voltage_limit, cmd); }
void doMode  (char *cmd) {
    int m = cmd ? atoi(cmd) : 1;
    if      (m == 0) motor.controller = MotionControlType::torque;
    else if (m == 2) motor.controller = MotionControlType::angle;
    else             motor.controller = MotionControlType::velocity;
    Serial.print("mode = ");
    Serial.println(m == 0 ? "torque(力矩)" : (m == 2 ? "angle(位置)" : "velocity(速度)"));
}

/* ---------- 4. 屏幕绘制 ---------- */
static void drawOLED()
{
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tf);

    u8g2.drawStr(0, 11, "F407 FOC closed");
    u8g2.drawHLine(0, 14, 128);

    const char *mode = (motor.controller == MotionControlType::angle)  ? "POS"
                      : (motor.controller == MotionControlType::torque) ? "TRQ"
                      : "VEL";
    char buf[24];
    snprintf(buf, sizeof(buf), "mode %s", mode);
    u8g2.drawStr(0, 28, buf);

    // 目标：速度环显示 rad/s，位置环显示 弧度 / 圈数
    if (motor.controller == MotionControlType::angle) {
        snprintf(buf, sizeof(buf), "Tgt %6.2f rad", motor.target);
        u8g2.drawStr(0, 40, buf);
        snprintf(buf, sizeof(buf), "    %6.2f rev", motor.target / (2.0f * PI));
    } else {
        snprintf(buf, sizeof(buf), "Tgt %6.2f rad/s", motor.target);
        u8g2.drawStr(0, 40, buf);
        snprintf(buf, sizeof(buf), "    %6.1f rpm", motor.target * 60.0f / (2.0f * PI));
    }
    u8g2.drawStr(0, 52, buf);

    // 实测值（来自 AS5600，闭环才是真值；屏幕只放角度，速度看串口更直观）
    snprintf(buf, sizeof(buf), "Ang %6.1f deg", motor.shaftAngle() * 180.0f / PI);
    u8g2.drawStr(0, 63, buf);

    u8g2.sendBuffer();
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

    oled_ok = u8g2.begin();
    Serial.println(oled_ok ? "OLED OK" : "OLED 未找到（不影响闭环）");

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
    motor.LPF_velocity.Tf = 0.01f;    // 速度低通滤波时间常数
    // 位置环 P（位置环内部再套一层速度环）
    motor.P_angle.P = 20.0f;
    motor.controller = MotionControlType::velocity;

    motor.init();
    motor.initFOC();                  // ★ 电角度对齐：上电会轻微动一下，正常

    command.add('T', doTarget, "target (rad/s 或 rad)");
    command.add('L', doLimit,  "voltage limit (V)");
    command.add('M', doMode,   "mode 0=trq 1=vel 2=pos");

    Serial.println("就绪。T<速度> 调速；M2 切位置环后 T<弧度> 定位；L<电压> 调上限");
}

/* ---------- 6. 主循环 ---------- */
void loop()
{
    motor.loopFOC();          // FOC 调制 + 读编码器（闭环核心，必须每帧调）
    motor.move();              // 执行运动控制（用 motor.target）
    command.run();            // 处理串口命令

    static uint32_t last_oled = 0;
    if (oled_ok && millis() - last_oled > OLED_REFRESH_MS) {
        last_oled = millis();
        drawOLED();
    }

    static uint32_t last_led = 0;
    if (millis() - last_led > 500) {
        last_led = millis();
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
}
