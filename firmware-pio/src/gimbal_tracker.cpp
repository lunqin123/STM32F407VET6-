/* ============================================================
 * STM32F407VET6 + SimpleFOC + AS5600 —— 云台视觉追踪（单轴 / 水平）
 *
 * 【架构：感知在 PC，控制在 MCU】
 *   PC(OpenCV 摄像头) 检测目标 → 像素误差 → 增量 PID → 目标角
 *      ↓ 串口 115200 发 "T<弧度>\n"（vision/vision_tracker.py）
 *   F407 角度环(SimpleFOC) → 三相 PWM → 驱动板 → 电机转到目标角
 *
 * 【与 closedloop.cpp 的区别】
 *   1. 默认就是角度模式（视觉伺服只发目标角）
 *   2. 上电 target = 当前角度（不回零，摄像头不会开机甩头）
 *   3. velocity_limit 压到 5 rad/s（云台要稳，不要猛甩）
 *
 * 【串口命令】（PC 脚本只用 T；M/L 供手动调试）
 *   T<数字>  目标角（弧度，相对上电时刻）
 *   L<数字>  电压上限（起步 2V）
 *   M<0/1/2> 0力矩 1速度 2角度（默认 2）
 *
 * 【引脚】PWM=PA8/9/10(TIM1_CH1/2/3)，EN=PB0，I2C=PB7(SDA)/PB6(SCL)，LED=PC13
 * 【安全】initFOC() 上电会轻微动一下做电角度对齐，正常。
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <SimpleFOC.h>
#include <U8g2lib.h>

/* ---------- 1. 硬件参数区 ---------- */
#define POLE_PAIRS     7        // C2208-100T = 7 极对
#define PIN_UH         PA8      // U 相 → TIM1_CH1
#define PIN_VH         PA9      // V 相 → TIM1_CH2
#define PIN_WH         PA10     // W 相 → TIM1_CH3
#define PIN_EN         PB0      // 驱动板使能（拉高才输出）
#define LED_PIN        PC13
#define OLED_SDA       PB7      // ★ F407 的 Wire 默认 SDA=PB7（非 UNO 习惯）
#define OLED_SCL       PB6

#define SUPPLY_VOLTAGE 12.0f
#define VOLTAGE_LIMIT  2.0f     // 起步限压，防过流
#define OLED_REFRESH_MS 100

/* ---------- 2. 对象 ---------- */
BLDCMotor        motor  = BLDCMotor(POLE_PAIRS);
BLDCDriver3PWM   driver = BLDCDriver3PWM(PIN_UH, PIN_VH, PIN_WH, PIN_EN);
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

bool oled_ok = false;

/* ---------- 3. 串口命令 ---------- */
Commander command = Commander(Serial);
void doTarget(char *cmd) { command.scalar(&motor.target, cmd); }
void doLimit (char *cmd) { command.scalar(&motor.voltage_limit, cmd); }
void doMode  (char *cmd) {
    int m = cmd ? atoi(cmd) : 2;
    if      (m == 0) motor.controller = MotionControlType::torque;
    else if (m == 1) motor.controller = MotionControlType::velocity;
    else             motor.controller = MotionControlType::angle;
    Serial.print("mode = ");
    Serial.println(m == 0 ? "torque" : (m == 1 ? "velocity" : "angle(vision)"));
}

/* ---------- 4. 屏幕绘制 ---------- */
static void drawOLED()
{
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tf);

    u8g2.drawStr(0, 11, "VISION TRACK");
    u8g2.drawHLine(0, 14, 128);

    char buf[24];
    snprintf(buf, sizeof(buf), "Tgt %7.1f deg", motor.target * 180.0f / PI);
    u8g2.drawStr(0, 30, buf);
    /* ★ 直读传感器而非 motor.shaftAngle()：shaftAngle 返回的是 loopFOC
     *   维护的缓存，无 12V 时 initFOC 对齐失败 → loopFOC 每圈跳过 → 缓存恒 0。
     *   注意用 getMechanicalAngle()（单圈 0~2π 会 wrap），不要用 getAngle()——
     *   那是带累计圈数的连续角，转轴会一直累加到 ±∞。 */
    float now = sensor.getMechanicalAngle();
    snprintf(buf, sizeof(buf), "Now %7.1f deg", now * 180.0f / PI);
    u8g2.drawStr(0, 44, buf);
    float err = motor.target - now;
    snprintf(buf, sizeof(buf), "Err %7.1f deg", err * 180.0f / PI);
    u8g2.drawStr(0, 58, buf);

    u8g2.sendBuffer();
}

/* ---------- 5. 初始化 ---------- */
void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);

    Serial.begin(115200);
    delay(1500);

    Serial.println("\n===== STM32F407VET6 · 云台视觉追踪 (单轴) =====");

    /* I2C：先 setSDA/SCL 再 begin；AS5600 与 OLED 同总线 */
    Wire.setSDA(OLED_SDA);
    Wire.setSCL(OLED_SCL);
    Wire.begin();
    Wire.setClock(400000);

    oled_ok = u8g2.begin();
    Serial.println(oled_ok ? "OLED OK" : "OLED 未找到（不影响追踪）");

    sensor.init();

    driver.voltage_power_supply = SUPPLY_VOLTAGE;
    driver.init();
    driver.enable();
    motor.linkDriver(&driver);
    motor.linkSensor(&sensor);

    /* 控制参数：视觉伺服 = 角度模式 + 平稳的速度上限 */
    motor.voltage_limit  = VOLTAGE_LIMIT;
    motor.velocity_limit = 5.0f;      // 云台要稳，宁慢勿抖
    motor.PID_velocity.P = 0.2f;
    motor.PID_velocity.I = 20.0f;
    motor.PID_velocity.D = 0.0f;
    motor.LPF_velocity.Tf = 0.01f;
    motor.P_angle.P = 15.0f;          // 位置环 P（比 closedloop 略柔）
    motor.controller = MotionControlType::angle;   // ★ 视觉伺服默认角度模式

    motor.init();
    motor.initFOC();

    /* ★ 关键：上电目标 = 当前实际角度，摄像头不会开机甩头 */
    motor.target = motor.shaftAngle();
    Serial.print("初始角度 = ");
    Serial.print(motor.target * 180.0f / PI);
    Serial.println(" deg（这是摄像头正前方的零点）");

    command.add('T', doTarget, "target angle (rad)");
    command.add('L', doLimit,  "voltage limit (V)");
    command.add('M', doMode,   "mode 0=trq 1=vel 2=angle");

    Serial.println("就绪。等待 PC 视觉脚本发 T<角度> ...");
}

/* ---------- 6. 主循环 ---------- */
void loop()
{
    motor.loopFOC();
    motor.move();
    command.run();

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
