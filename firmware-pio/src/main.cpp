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
 *   BLDC 电机 + 三相驱动板（推荐：Makerbase MKS SimpleFOC Shield V2.0.4，L6234 驱动级）
 *   SSD1306 0.96" 128x64 I2C OLED（4 针：VCC GND SCL SDA，地址 0x3C）
 *   开环模式【不需要编码器】，先把电机转起来；闭环下一步再加
 *
 *   ⚠️ 关于 MKS SimpleFOC Shield V2.0.4（已核实为「驱动级」，非自带 MCU 的控制器）：
 *     - 驱动芯片 L6234，3 路 PWM 输入由外部 MCU（本板 F407）提供 → SimpleFOC 兼容 ✅
 *     - 供电 12–35V；本电机额定 12V，**务必用 12V 电源**，别上 35V
 *     - 自带 INA240 电流采样 + 编码器 I2C 接口 → 以后做力矩环/接 AS5600 很方便
 *     - 注意别买成「MKS ESP32 FOC」（那款自带 ESP32 MCU，会跳过 F407，不是我们要的）
 *     - 板子 PWM 输入在 Arduino 排针上（SimpleFOC Shield v2 默认 IN1=D9/IN2=D5/D3 或 D6、EN=D8，
 *       具体以你板子丝印/0R 跳线为准）。F407 PA8/9/10 → 三个 PWM 输入，PB0 → EN。
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
#define PIN_EN           PB0      // 驱动板使能脚 → MKS Shield 的 EN 引脚（D8 默认）
                                 // 若你的板子 EN 已用 0R 电阻拉高，这脚接不接都行；接上更稳
#define LED_PIN          PC13     // 板载 LED（低电平点亮）

#define OLED_SDA         PB7      // ★ 见文件头说明：F407 的 Wire 默认 SDA 是 PB7
#define OLED_SCL         PB6      //                              SCL 是 PB6

#define SUPPLY_VOLTAGE   12.0f    // 驱动板供电电压（按你的电源改）
#define VOLTAGE_LIMIT    2.0f     // ★ 起步限压，防止过流烧电机/驱动板

#define OLED_REFRESH_MS  100      // 屏幕刷新周期（10Hz）——不要再快，I2C 会拖慢控制环

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
/* 全缓冲模式：先在内存里画，再一次 sendBuffer 推给屏幕，不会闪 */
static void drawOLED()
{
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tf);

    u8g2.drawStr(0, 11, "F407 + SimpleFOC");
    u8g2.drawHLine(0, 14, 128);

    char buf[24];

    // 目标转速（rad/s 换成 rpm 更直观）
    snprintf(buf, sizeof(buf), "Tgt %6.2f rad/s", target_velocity);
    u8g2.drawStr(0, 28, buf);
    snprintf(buf, sizeof(buf), "    %6.1f rpm", target_velocity * 60.0f / (2.0f * PI));
    u8g2.drawStr(0, 40, buf);

    // 开环下 motor.shaftAngle() 是「估算角度」，不是实测——闭环接上编码器后才是真值
    snprintf(buf, sizeof(buf), "Ang %6.1f deg", motor.shaftAngle() * 180.0f / PI);
    u8g2.drawStr(0, 52, buf);

    snprintf(buf, sizeof(buf), "Ulim %.1fV", motor.voltage_limit);
    u8g2.drawStr(0, 63, buf);

    u8g2.sendBuffer();
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
    driver.enable();                    // 拉高 EN 脚，驱动级才开始输出三相（MKS Shield 必需）
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

    /* OLED：10Hz 刷新，不阻塞电机控制 */
    static uint32_t last_oled = 0;
    if (oled_ok && millis() - last_oled > OLED_REFRESH_MS) {
        last_oled = millis();
        drawOLED();
    }

    /* 心跳灯：屏幕之外留个"程序还活着"的物理指示（PC13 低电平点亮） */
    static uint32_t last_led = 0;
    if (millis() - last_led > 500) {
        last_led = millis();
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
}
