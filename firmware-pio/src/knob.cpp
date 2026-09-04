/* ============================================================
 * 无动力智能旋钮（AS5600 输入设备）—— 不需要 12V！
 *
 * 转电机轴 = 转旋钮：OLED 实时显示 0~100 的数值和进度条。
 * 电机不通电，纯靠轴上的磁编码器感知旋转 —— 它就是个输入设备。
 *
 * 【原理】
 *   连续跟踪单圈角度，跨 0/2π 时做 wrap 补偿，增量按比例
 *   累加成旋钮值。这是增量式编码器的标准读法（光电滚轮同理）。
 *
 * 【今晚三个调试成果都用上了】
 *   1. sensor.getMechanicalAngle() 直读（绕开 SimpleFOC 的 loopFOC 缓存）
 *   2. 单圈 wrap：|Δ|>π 视为跨圈，补偿 2π
 *   3. newlib-nano 浮点显示（build_flags 的 -Wl,-u,_printf_float）
 *
 * 【12V 到位后的升级方向】FOC 力控可实现：
 *   棘轮刻度感（周期性力阱）、阻尼、回中弹簧、边界墙 ——
 *   即 SimpleFOC 社区的 Smart Knob / Haptic Knob 项目。届时
 *   输入（本文件的角度跟踪）与输出（力矩波形）叠加在同一根轴上。
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <SimpleFOC.h>
#include <U8g2lib.h>

#define LED_PIN         PC13
#define SDA_PIN         PB7
#define SCL_PIN         PB6

#define KNOB_MIN        0
#define KNOB_MAX        100
#define UNITS_PER_TURN  50.0f     // 电机轴转一整圈 = 旋钮值变化 50

/* --- 屏幕管理（免按钮）--- */
#define SCREEN_TIMEOUT_MS 10000UL // 10 秒无转动自动熄屏（防烧屏 + 省电）
#define MOTION_WINDOW_MS  100UL   // 运动检测窗口
/* 窗口内累计 |Δ角| 超过此值即算"在动"。0.01rad(0.57°)/100ms = 5.7°/s，
 * 慢转也能唤醒；AS5600 随机噪声正负相消约 0.3°，不会误触发。 */
#define MOTION_ACCUM      0.01f
/* ★ 熄屏实现 = 推一帧全黑，而不是 u8g2.setPowerSave(1)。
 *   setPowerSave 的 display-off 命令后 I2C 总线可能被 SSD1306 卡死，
 *   同总线的 AS5600 读数随之冻结 → 转轴唤醒永远检测不到。
 *   全黑帧视觉等同关屏（像素不亮=防烧屏），且总线保持活动。 */
bool     screen_on  = true;
uint32_t last_motion = 0;         // 上次检测到转动的时刻
uint32_t last_oled   = 0;
float    motion_sum  = 0.0f;      // 窗口内累计转角
uint32_t last_motion_check = 0;

MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

float last_raw = 0.0f;
/* ★ 必须用浮点累加器：100Hz 下慢转时每帧增量 < 0.5 单位，
 *   若每帧 roundf 取整再加，增量全部丢失 → 转了没反应。
 *   浮点攒着，显示/判断时才取整，慢转也不丢步。 */
float knob_f   = 50.0f;
int   last_shown = -999;

void setup()
{
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);

    Serial.begin(115200);
    delay(1500);

    Wire.setSDA(SDA_PIN);
    Wire.setSCL(SCL_PIN);
    Wire.begin();
    Wire.setClock(400000);

    bool oled_ok = u8g2.begin();
    Serial.println("\n===== 无动力智能旋钮 (AS5600, 无需 12V) =====");
    Serial.println(oled_ok ? "OLED OK" : "OLED 未找到（不影响串口读数）");

    sensor.init();                      // 只初始化传感器，不碰 motor/driver
    sensor.update();                    // 先拉一次真实角度
    last_raw = sensor.getMechanicalAngle();
    last_motion = millis();

    Serial.println("转电机轴调整数值（转一圈 = 50），串口实时回显");
}

void loop()
{
    /* ★ SimpleFOC 的 Sensor 是 pull 模型：所有 getter 返回的都是内部
     *   缓存，必须有人调 update() 才会真正读芯片。gimbal 固件里是
     *   motor.loopFOC() 在调；本固件没有 motor，必须自己每帧调一次，
     *   否则读数永远是上电那一刻的 0。 */
    sensor.update();

    /* --- 角度跟踪：wrap 补偿 + 增量累加（100Hz 足够） --- */
    float raw = sensor.getMechanicalAngle();          // 单圈角 0~2π
    float d = raw - last_raw;
    if (d > PI)  d -= 2 * PI;                         // 从 2π 跨到 0：实际是正转一小步
    if (d < -PI) d += 2 * PI;                         // 反向同理
    last_raw = raw;

    knob_f = constrain(knob_f + d * (UNITS_PER_TURN / (2.0f * PI)),
                       (float)KNOB_MIN, (float)KNOB_MAX);
    int knob = (int)knob_f;

    /* --- 运动检测：100ms 滑动窗口累计（灵敏版，替代单帧阈值） ---
     * 单帧比较在 100Hz 下要 28°/s 才触发，慢转唤不醒；
     * 窗口累计只需 5.7°/s。 */
    motion_sum += fabsf(d);
    if (millis() - last_motion_check > MOTION_WINDOW_MS) {
        bool moving = motion_sum > MOTION_ACCUM;
        motion_sum = 0;
        last_motion_check = millis();
        if (moving) {
            last_motion = millis();
            if (!screen_on) {
                screen_on = true;
                last_oled = 0;                        // 立即重画
            }
        }
    }

    /* --- 串口命令 P：手动开关屏幕（连电脑时可用） --- */
    if (Serial.available()) {
        char c = toupper(Serial.read());
        if (c == 'P') {
            screen_on = !screen_on;
            if (!screen_on) {                         // 熄屏 = 推一帧全黑
                u8g2.clearBuffer();
                u8g2.sendBuffer();
            }
            last_oled = 0;                            // 开屏后立即重画一帧
            Serial.println(screen_on ? "OLED ON" : "OLED OFF");
        }
    }

    /* --- 超时熄屏 --- */
    if (screen_on && millis() - last_motion > SCREEN_TIMEOUT_MS) {
        screen_on = false;
        u8g2.clearBuffer();                           // 推一帧全黑
        u8g2.sendBuffer();
        Serial.println("OLED sleep (10s idle)");      // 顺带防烧屏
    }

    /* --- 到达边界时 LED 亮，作为最朴素的"提示器" --- */
    digitalWrite(LED_PIN, (knob <= KNOB_MIN || knob >= KNOB_MAX) ? LOW : HIGH);

    /* --- 串口：值变化才打印 --- */
    if (knob != last_shown) {
        Serial.print("knob = ");
        Serial.println(knob);
        last_shown = knob;
    }

    /* --- OLED 10Hz：大数字 + 进度条（熄屏时完全跳过刷写） --- */
    if (screen_on && millis() - last_oled > 100) {
        last_oled = millis();
        u8g2.clearBuffer();
        u8g2.setFont(u8g2_font_6x12_tf);
        u8g2.drawStr(0, 12, "SMART KNOB  (no 12V)");

        u8g2.setFont(u8g2_font_logisoso28_tn);        // 28px 数字字体
        char buf[8];
        snprintf(buf, sizeof(buf), "%d", knob);
        u8g2.drawStr(64 - u8g2.getStrWidth(buf) / 2, 42, buf);

        u8g2.drawFrame(4, 48, 120, 12);
        int w = (int)(116.0f * (knob - KNOB_MIN) / (KNOB_MAX - KNOB_MIN));
        u8g2.drawBox(6, 50, w, 8);
        u8g2.sendBuffer();
    }
}
