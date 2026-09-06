/* ============================================================
 * STM32F407VET6 + SimpleFOC + SSD1306 OLED — 开环转动 + 实时读数
 *
 * 【这就是"调用现成库"的写法】
 *   裸机手写寄存器：要点亮一个 LED 需要 39 行 + 翻手册算地址（0x40023830…）
 *   用库：让一个无刷电机转起来 + 屏幕显示，全程不碰寄存器地址
 *
 * 【引脚依据】
 *   PWM : PA8/PA9/PA10 = TIM1_CH1/2/3（来自 00-CHIP-REFERENCE/03-PINOUT/pins.csv）
 *   I2C : PB7 = SDA, PB6 = SCL
 *         ★ 这不是猜的：实测 framework-arduinoststm32 的
 *           variants/STM32F4xx/F407V(E-G)T_F417V(E-G)T/variant_generic.h 第 184–188 行
 *           #define PIN_WIRE_SDA  PB7
 *           #define PIN_WIRE_SCL  PB6
 *         很多网贴写成 PB6=SDA/PB7=SCL（UNO 的习惯），在 F407 上是反的，接反不亮。
 *
 * 【硬件】
 *   BLDC 电机 + 三相驱动板（L6234 模块 / DRV8302 / SimpleFOC Shield）
 *   SSD1306 0.96" 128x64 I2C OLED（4 针：VCC GND SCL SDA，地址 0x3C）
 *   开环模式【不需要编码器】，先把电机转起来；闭环下一步再加
 *
 * 【安全】VOLTAGE_LIMIT 起步务必设小（2V 左右），确认转向正常后再逐步提高
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <SimpleFOC.h>
#include <U8g2lib.h>

/* ---------- 1. 硬件参数区：换电机 / 改接线，只动这里 ---------- */
#define POLE_PAIRS       7        // 电机极对数（云台电机常见 7；不确定先填 7 试）
#define PIN_UH           PA8      // U 相上桥 → TIM1_CH1
#define PIN_VH           PA9      // V 相上桥 → TIM1_CH2
#define PIN_WH           PA10     // W 相上桥 → TIM1_CH3
#define PIN_EN           PB0      // ★ Shield V3.2：DRV8313 的 Enable，不拉高不输出
#define LED_PIN          PC13     // 板载 LED（低电平点亮）

#define OLED_SDA         PB7      // ★ 见文件头说明：F407 的 Wire 默认 SDA 是 PB7
#define OLED_SCL         PB6      //                              SCL 是 PB6

#define SUPPLY_VOLTAGE   12.0f    // 驱动板供电电压（按你的电源改）
#define VOLTAGE_LIMIT    2.0f     // ★ 起步限压，防止过流烧电机/驱动板

// （已弃用）刷新已改为事件驱动，见 drawOLED 处注释

/* ---------- 2. 对象 ---------- */
BLDCMotor       motor   = BLDCMotor(POLE_PAIRS);
BLDCDriver3PWM  driver  = BLDCDriver3PWM(PIN_UH, PIN_VH, PIN_WH, PIN_EN);

// 硬件 I2C / 全缓冲（1KB RAM）/ 无复位脚。买成 SH1106 1.3" 就把这行换成
// U8G2_SH1106_128X64_NONAME_F_HW_I2C，其余代码不用动——这是选 U8g2 的好处。
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

float target_velocity = 2.0f;     // 目标转速，单位 rad/s
bool  oled_ok         = false;    // 屏幕没接也别让程序卡死

/* 串口命令解析器：让你不用重新烧录就能调参 */
Commander command = Commander(Serial);
void doTarget(char *cmd) { command.scalar(&target_velocity, cmd); }
void doLimit (char *cmd) {
    command.scalar(&motor.voltage_limit, cmd);
    Serial.print("voltage_limit = "); Serial.println(motor.voltage_limit);
}

/* ---------- 3. 屏幕绘制 ---------- */
/* ★ 事件驱动刷新（不要改回定时刷新！）：
 *   全缓冲帧 1KB @400kHz 要阻塞主循环 ~25-30ms。若 10Hz 定时刷新，
 *   电机每秒被"冻结"10 次 → 开环转动肉眼可见地一顿一顿（2026-09-06 实测）。
 *   改为只在 Tgt / Ulim 变化时画一次，平时循环里零 I2C 开销。
 *   代价：去掉实时角度行（开环下它本来就只是内部积分值，非实测）。 */
static float last_drawn_target = -1.0f;
static float last_drawn_ulim   = -1.0f;

static bool oledNeedsRedraw()
{
    return target_velocity != last_drawn_target
        || motor.voltage_limit  != last_drawn_ulim;
}

static void drawOLED()
{
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tf);

    u8g2.drawStr(0, 11, "F407 + SimpleFOC");
    u8g2.drawHLine(0, 14, 128);

    char buf[24];

    // 目标转速（rad/s 换成 rpm 更直观）
    snprintf(buf, sizeof(buf), "Tgt %6.2f rad/s", target_velocity);
    u8g2.drawStr(0, 30, buf);
    snprintf(buf, sizeof(buf), "    %6.1f rpm", target_velocity * 60.0f / (2.0f * PI));
    u8g2.drawStr(0, 44, buf);

    snprintf(buf, sizeof(buf), "Ulim %.1fV", motor.voltage_limit);
    u8g2.drawStr(0, 63, buf);

    u8g2.sendBuffer();

    last_drawn_target = target_velocity;
    last_drawn_ulim   = motor.voltage_limit;
}

/* ---------- 4. 初始化 ---------- */
void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);            // PC13 低电平点亮 → 先熄灭

    Serial.begin(115200);
    delay(1500);                            // 等 USB 串口枚举

    Serial.println("\n===== STM32F407VET6 · SimpleFOC 开环 + OLED =====");

    /* --- OLED 初始化：必须先 setSDA/setSCL 再 begin() --- */
    Wire.setSDA(OLED_SDA);
    Wire.setSCL(OLED_SCL);
    Wire.begin();
    Wire.setClock(400000);                  // 400kHz 快速模式，刷新更快
    // u8g2.begin() 内部会再调一次 Wire.begin()，重复调用无害
    oled_ok = u8g2.begin();
    Serial.println(oled_ok ? "OLED 初始化 OK" : "OLED 未找到（检查接线/I2C 地址 0x3C）");
    if (oled_ok) {
        u8g2.setFont(u8g2_font_6x12_tf);
        u8g2.clearBuffer();
        u8g2.drawStr(0, 20, "SimpleFOC");
        u8g2.drawStr(0, 36, "booting...");
        u8g2.sendBuffer();
    }

    /* --- 驱动板配置 --- */
    driver.voltage_power_supply = SUPPLY_VOLTAGE;
    Serial.println(driver.init() ? "驱动板初始化 OK" : "驱动板初始化失败");
    motor.linkDriver(&driver);

    /* 控制模式：开环速度 —— 不需要编码器，靠电压"硬拖"电机转 */
    motor.controller    = MotionControlType::velocity_openloop;
    motor.voltage_limit = VOLTAGE_LIMIT;
    motor.init();

    /* 注册串口命令 */
    command.add('T', doTarget, "target velocity (rad/s)");
    command.add('L', doLimit,  "voltage limit (V)");

    Serial.println("就绪。T<数字> 设转速（如 T5）；L<数字> 调限压（如 L3）");
    Serial.println("若电机抖动不转：调大 L，或检查极对数/相序");
}

/* ---------- 5. 主循环 ---------- */
void loop()
{
    motor.move(target_velocity);   // 开环：函数内部自带时序控制
    command.run();                 // 处理串口命令（非阻塞）

    /* OLED：★ 仅参数变化时重画（事件驱动），转动期间零 I2C 阻塞 */
    if (oled_ok && oledNeedsRedraw()) {
        drawOLED();
    }

    /* 心跳灯：屏幕之外留个"程序还活着"的物理指示（PC13 低电平点亮） */
    static uint32_t last_led = 0;
    if (millis() - last_led > 500) {
        last_led = millis();
          digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
}
