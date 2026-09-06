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
 * 【OLED】走 lib/Screen 公共层（两段式刷新 + 10s 无活动自动息屏 +
 *   黑帧兜底重推），本文件不再手写屏幕管理——见 lib/Screen/src/Screen.h。
 *
 * 【12V 到位后的升级方向】FOC 力控可实现：
 *   棘轮刻度感（周期性力阱）、阻尼、回中弹簧、边界墙 ——
 *   即 SimpleFOC 社区的 Smart Knob / Haptic Knob 项目。届时
 *   输入（本文件的角度跟踪）与输出（力矩波形）叠加在同一根轴上。
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>
#include <SimpleFOC.h>
#include <Screen.h>

#define LED_PIN         PC13
#define SDA_PIN         PB7
#define SCL_PIN         PB6

#define KNOB_MIN        0
#define KNOB_MAX        100
#define UNITS_PER_TURN  50.0f     // 电机轴转一整圈 = 旋钮值变化 50

/* --- 屏幕管理（lib/Screen）--- */
#define SCREEN_TIMEOUT_MS 10000UL // 10 秒无转动自动息屏（防烧屏 + 省电）
#define MOTION_WINDOW_MS  100UL   // 运动检测窗口
/* 窗口内累计 |Δ角| 超过此值即算"在动"。0.02rad(1.15°)/100ms = 11.5°/s，
 * 慢转也能保活；AS5600 随机噪声正负相消约 0.3°，不会误触发。 */
#define MOTION_ACCUM      0.02f

MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);

float last_raw = 0.0f;
/* ★ 必须用浮点累加器：100Hz 下慢转时每帧增量 < 0.5 单位，
 *   若每帧 roundf 取整再加，增量全部丢失 → 转了没反应。
 *   浮点攒着，显示/判断时才取整，慢转也不丢步。 */
float knob_f   = 50.0f;
int   last_shown = -999;
float    motion_sum  = 0.0f;      // 窗口内累计转角
uint32_t last_motion_check = 0;

/* --- 屏幕绘制回调（内容由 lib/Screen 调度）--- */
/* 带0 静态：标题（tiles 0-1，基线 12） */
static void drawTitle(U8G2 &u)
{
    u.setFont(u8g2_font_6x12_tf);
    u.drawStr(0, 12, "SMART KNOB");
}
/* 带1 动态：大数字（tiles 2-5，基线 45，logisoso28 字形高约 28px） */
static void drawValue(U8G2 &u)
{
    int knob = (int)knob_f;
    u.setFont(u8g2_font_logisoso28_tn);
    char buf[8];
    snprintf(buf, sizeof(buf), "%d", knob);
    u.drawStr(64 - u.getStrWidth(buf) / 2, 45, buf);
}
/* 带2 动态：进度条（tiles 6-7，y 48-60 区域） */
static void drawBar(U8G2 &u)
{
    int knob = (int)knob_f;
    int w = (int)(116.0f * (knob - KNOB_MIN) / (KNOB_MAX - KNOB_MIN));
    u.drawFrame(4, 48, 120, 12);
    u.drawBox(6, 50, w, 8);
}

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

    bool oled_ok = oled::begin();
    Serial.println("\n===== 无动力智能旋钮 (AS5600, 无需 12V) =====");
    Serial.println(oled_ok ? "OLED OK" : "OLED 未找到（不影响串口读数）");
    if (oled_ok) {
        oled::addBand(0, 2, false, drawTitle);   // 静态：标题
        oled::addBand(2, 4, true,  drawValue);   // 动态：大数字（4 tile 局部重发）
        oled::addBand(6, 2, true,  drawBar);     // 动态：进度条
        oled::setSleepTimeout(SCREEN_TIMEOUT_MS);
    }

    sensor.init();                      // 只初始化传感器，不碰 motor/driver
    sensor.update();                    // 先拉一次真实角度
    last_raw = sensor.getMechanicalAngle();
    last_motion_check = millis();

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

    /* --- 运动检测 → 屏幕保活（100ms 滑动窗口累计，灵敏版） ---
     * 检测到转动就调 oled::activity()：阻止息屏 + 息屏中自动唤醒。
     * Screen 内部节流，不会因为频繁调用而多刷屏。 */
    motion_sum += fabsf(d);
    if (millis() - last_motion_check > MOTION_WINDOW_MS) {
        bool moving = motion_sum > MOTION_ACCUM;
        motion_sum = 0;
        last_motion_check = millis();
        if (moving) oled::activity();
    }

    /* --- 串口命令 P：手动开关屏幕 --- */
    if (Serial.available()) {
        char c = toupper(Serial.read());
        if (c == 'P') {
            if (oled::isAwake()) oled::sleep();
            else                 oled::wake();
            Serial.println(oled::isAwake() ? "OLED ON" : "OLED OFF");
        }
    }

    /* --- 数值/进度条变化 → 标脏对应带（息屏中会自动唤醒重画） --- */
    if (knob != last_shown) {
        oled::invalidate(1);
        oled::invalidate(2);
        Serial.print("knob = ");
        Serial.println(knob);
        last_shown = knob;
    }

    /* --- 到达边界时 LED 亮，作为最朴素的"提示器" --- */
    digitalWrite(LED_PIN, (knob <= KNOB_MIN || knob >= KNOB_MAX) ? LOW : HIGH);

    /* --- 屏幕调度（内部：静态带事件驱动 / 动态带局部重发 / 息屏兜底） --- */
    oled::tick();
}
