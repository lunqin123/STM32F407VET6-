/* ============================================================
 * STM32F407VET6 — 硬件到货自检固件（不依赖电机，先验证"线接对了没"）
 *
 * 【为什么要有这个东西】
 *   电机、驱动板、OLED 同时到货时，一次性全接上→不转，你会面对 4 个新变量
 *   同时失效，根本没法定位。这个固件让你**逐个验证**，把"不知道哪错了"
 *   变成"已知 A 对、B 对、C 有问题"。
 *
 * 【上电自动跑】I2C 扫描 → OLED 测试 → LED 闪烁（不需要接电脑也能看 OLED 结果）
 * 【串口菜单】按 1/2/3/4 或 S/O/P/L 逐项重跑，A = 全部跑一遍
 *
 * 【用法】
 *   1. 只插 USB（先别接电机/驱动板）→ 打开串口监视器 115200
 *   2. 看到 I2C 扫描结果；OLED 单独接上就能测屏幕
 *   3. 按 P 做 PWM 测试，用万用表量 PA8/PA9/PA10 对地电压
 *      ★ 测 PWM 时不要接电机，只验证信号是否存在
 *
 * 【引脚】PB7=SDA / PB6=SCL（★ 不是 UNO 的 PB6=SDA/PB7=SCL，接反屏幕全黑不亮）
 *        PA8/PA9/PA10 = TIM1_CH1/2/3，PC13 = 板载 LED（低电平点亮）
 *
 * 编译/烧录：pio run -e selftest -t upload
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>

#define OLED_SDA   PB7
#define OLED_SCL   PB6
#define LED_PIN    PC13
#define PIN_UH     PA8
#define PIN_VH     PA9
#define PIN_WH     PA10

#define PWM_FREQ_HZ   1000      // 自检用的 PWM 频率
#define PWM_DUTY_PCT  20        // 20% 占空比 → 万用表应量到 3.3V × 20% ≈ 0.66V
#define PWM_TEST_MS   10000     // PWM 测试持续 10 秒后自动停

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);
bool oled_ok = false;

/* 常见外设的 I2C 地址速查表（7 位地址） */
struct Dev { uint8_t addr; const char *name; };
static const Dev KNOWN[] = {
    {0x3C, "SSD1306 / SH1106 OLED"},
    {0x3D, "SSD1306 OLED（备用地址）"},
    {0x36, "AS5600 磁编码器"},
    {0x40, "INA219 电流/电压计"},
    {0x68, "MPU6050 六轴 IMU"},
};

static uint8_t found[128];
static int     nfound = 0;

/* ---------- 1. I2C 扫描 ---------- */
static void scanI2C()
{
    nfound = 0;
    Serial.println("\n----- I2C 扫描 (SDA=PB7, SCL=PB6) -----");

    for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            found[nfound++] = addr;

            const char *name = "";
            for (const Dev &d : KNOWN) {
                if (d.addr == addr) { name = d.name; break; }
            }
            char buf[64];
            snprintf(buf, sizeof(buf), "  找到 0x%02X  %s", addr, name);
            Serial.println(buf);
        }
    }

    if (nfound == 0) {
        Serial.println("  ✗ 没找到任何 I2C 设备");
        Serial.println("    检查：VCC/GND 接了吗？SDA→PB7 / SCL→PB6 有没有接反？");
        Serial.println("    （SDA/SCL 接反是最常见原因，屏幕不会烧，放心对调试）");
    } else {
        Serial.print("  共 "); Serial.print(nfound); Serial.println(" 个设备");
    }
    Serial.println("---------------------------------------");
}

/* ---------- 2. OLED 测试 ---------- */
static void testOLED()
{
    Serial.println("\n----- OLED 测试 -----");
    if (!oled_ok) {
        Serial.println("  ✗ 屏幕初始化失败（或 I2C 扫描没找到 0x3C）");
        Serial.println("    屏幕没接时这是正常的，不影响电机自检");
        return;
    }

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tf);

    u8g2.drawStr(0, 11, "F407 SELF TEST");
    u8g2.drawHLine(0, 14, 128);
    u8g2.drawFrame(0, 18, 128, 30);          // 画个框，验证全屏无坏点/无错位

    char buf[24];
    snprintf(buf, sizeof(buf), "I2C found: %d", nfound);
    u8g2.drawStr(4, 31, buf);

    if (nfound > 0) {
        snprintf(buf, sizeof(buf), "0x%02X", found[0]);
        u8g2.drawStr(4, 43, buf);
    } else {
        u8g2.drawStr(4, 43, "no device");
    }

    u8g2.drawStr(0, 58, "OLED OK");
    u8g2.drawStr(0, 63, "see serial for menu");
    u8g2.sendBuffer();

    Serial.println("  ✓ 已向屏幕输出测试图案");
    Serial.println("    能看到 [F407 SELF TEST] + 一个方框 + I2C 数量吗？");
    Serial.println("    若显示错位/左边缺 2 像素 → 你买到的可能是 SH1106 1.3\"，");
    Serial.println("    把本文件第 41 行改成 U8G2_SH1106_128X64_NONAME_F_HW_I2C 即可");
}

/* ---------- 3. LED 闪烁 ---------- */
static void testLED()
{
    Serial.println("\n----- LED 测试（PC13，闪 5 下）-----");
    for (int i = 0; i < 5; i++) {
        digitalWrite(LED_PIN, LOW);     // PC13 低电平点亮
        delay(150);
        digitalWrite(LED_PIN, HIGH);
        delay(150);
    }
    Serial.println("  ✓ 板载 LED 应闪烁 5 次。不亮则检查是不是 PC13 被占用");
}

/* ---------- 4. PWM 输出测试 ---------- */
static void testPWM()
{
    Serial.println("\n----- PWM 测试（PA8/PA9/PA10）-----");
    Serial.println("  ★ 此时请先断开电机，只留万用表/示波器");
    Serial.println("  10 秒后自动停止，期间每 2 秒打印一次剩余时间");

    analogWriteResolution(8);
    analogWriteFrequency(PWM_FREQ_HZ);
    uint8_t duty = (uint8_t)(255 * PWM_DUTY_PCT / 100);

    analogWrite(PIN_UH, duty);
    analogWrite(PIN_VH, duty);
    analogWrite(PIN_WH, duty);

    for (int t = PWM_TEST_MS; t > 0; t -= 2000) {
        Serial.print("    剩 "); Serial.print(t / 1000); Serial.println(" 秒...");
        delay(2000);
    }

    analogWrite(PIN_UH, 0);
    analogWrite(PIN_VH, 0);
    analogWrite(PIN_WH, 0);

    Serial.print("  ✓ 已输出 "); Serial.print(PWM_FREQ_HZ);
    Serial.print("Hz / "); Serial.print(PWM_DUTY_PCT); Serial.println("% 占空比，已停止");
    Serial.println("    用万用表直流档量 PA8/PA9/PA10 对地，应约为 0.66V（3.3V × 20%）");
    Serial.println("    三路都≈0.66V = TIM1 与引脚配置正确，可以放心接驱动板了");
    Serial.println("    若全是 0V 或 3.3V → 引脚定义或接线有问题，别急着接电机");
}

/* ---------- 菜单 ---------- */
static void showMenu()
{
    Serial.println("\n================ 菜单 ================");
    Serial.println("  1 / S  I2C 扫描");
    Serial.println("  2 / O  OLED 测试");
    Serial.println("  3 / P  PWM 测试（先断开电机！）");
    Serial.println("  4 / L  LED 闪烁");
    Serial.println("  A      全部跑一遍");
    Serial.println("======================================");
}

/* ---------- 初始化 ---------- */
void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);        // PC13 低电平点亮 → 先熄灭

    Serial.begin(115200);
    delay(2000);                        // 等 USB CDC 枚举

    Serial.println("\n╔══════════════════════════════════════╗");
    Serial.println("║   STM32F407VET6 硬件到货自检 v1.0    ║");
    Serial.println("╚══════════════════════════════════════╝");
    Serial.println("上电先自动跑：I2C 扫描 → OLED 测试 → LED 闪烁\n");

    /* I2C：必须先 setSDA/setSCL 再 begin()，之后 u8g2.begin() 会再调一次，重复无害 */
    Wire.setSDA(OLED_SDA);
    Wire.setSCL(OLED_SCL);
    Wire.begin();
    Wire.setClock(400000);

    oled_ok = u8g2.begin();

    /* 自动跑一遍安全项（不含 PWM，因为它会驱动电机） */
    scanI2C();
    testOLED();
    testLED();
    showMenu();
}

void loop()
{
    if (Serial.available()) {
        char c = Serial.read();
        c = toupper(c);
        switch (c) {
            case '1': case 'S': scanI2C();  break;
            case '2': case 'O': testOLED(); break;
            case '3': case 'P': testPWM();  break;
            case '4': case 'L': testLED();  break;
            case 'A':
                scanI2C(); testOLED(); testLED();
                Serial.println("\n（全部项已跑完，PWM 需手动按 P）");
                break;
            default: break;
        }
        if (strchr("1234SOPA", c)) showMenu();
    }
}
