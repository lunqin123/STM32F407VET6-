/* ============================================================
 * AS5600 底层诊断 —— 绕过 SimpleFOC，直读芯片寄存器
 *
 * 目的：I2C 扫描能发现 0x36 ≠ 读数有效。磁铁脱落/间隙过远/
 *       磁环型号不对时，角度寄存器会恒为 0 或不变。
 * 本固件直接读 4 个关键寄存器，一次定性问题在哪层。
 *
 * 判读指南（串口 115200）：
 *   I2C 读取失败        → 总线问题（接线/上拉/地址）
 *   STATUS bit3 (MD)=0  → 芯片说"没检测到磁铁" → 磁铁脱落或太远
 *   STATUS bit4 (ML)=1  → 磁场太弱 → 间隙加大或磁铁不匹配
 *   STATUS bit5 (MH)=1  → 磁场太强 → 磁铁太近（一般无害）
 *   MAG (0x1B)          → 磁场幅值，正常几百~2000；0 或极大都异常
 *   AGC (0x1A)          → 自动增益，磁铁合适时通常 0~100 之间
 *   手转电机轴：
 *   RAW 角度平滑跟随    → 编码器硬件完全正常，问题在 SimpleFOC 配置层
 *   RAW 恒 0 / 恒定不变 → 磁铁问题（脱落/没装/型号错）
 *   RAW 跳变            → 间隙临界，微调距离
 * ============================================================ */

#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN     PB7
#define SCL_PIN     PB6
#define AS5600_ADDR 0x36

#define REG_STATUS  0x0B    // 磁铁状态位
#define REG_RAWANG  0x0C    // 原始角度 (0~4095)
#define REG_AGC     0x1A    // 自动增益控制
#define REG_MAG     0x1B    // 磁场幅值

static uint16_t readReg16(uint8_t reg, bool *ok)
{
    *ok = false;
    Wire.beginTransmission(AS5600_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return 0;
    if (Wire.requestFrom(AS5600_ADDR, (uint8_t)2) != 2) return 0;
    uint16_t v = ((uint16_t)Wire.read() << 8) | Wire.read();
    *ok = true;
    return v;
}

void setup()
{
    Serial.begin(115200);
    delay(1500);

    pinMode(PC13, OUTPUT);
    digitalWrite(PC13, HIGH);

    Wire.setSDA(SDA_PIN);
    Wire.setSCL(SCL_PIN);
    Wire.begin();
    Wire.setClock(100000);          // 刻意用 100kHz：排除高速时序因素

    Serial.println("\n===== AS5600 底层诊断 (SDA=PB7 SCL=PB6, 100kHz) =====");
    Serial.println("转轴观察 RAW 角度；对照文件头判读 STATUS/AGC/MAG\n");
}

void loop()
{
    bool ok1, ok2, ok3, ok4;
    uint16_t raw    = readReg16(REG_RAWANG, &ok1);
    uint16_t status = readReg16(REG_STATUS, &ok2);
    uint16_t agc    = readReg16(REG_AGC,    &ok3);
    uint16_t mag    = readReg16(REG_MAG,    &ok4);

    if (!ok1 || !ok2 || !ok3 || !ok4) {
        Serial.println("!! I2C 读取失败（0x36 无应答）—— 查 SDA/SCL 接线与供电");
        digitalWrite(PC13, LOW);    // 出错时 LED 常亮
        delay(500);
        return;
    }
    digitalWrite(PC13, HIGH);

    uint8_t st   = status & 0xFF;
    bool md = st & 0x08;            // 磁铁已检测
    bool ml = st & 0x10;            // 磁场太弱
    bool mh = st & 0x20;            // 磁场太强
    float deg = raw * 360.0f / 4096.0f;

    char buf[120];
    snprintf(buf, sizeof(buf),
             "RAW=%4u (%6.1f deg) | ST=0x%02X [%s%s%s] | AGC=%3u | MAG=%4u",
             raw, deg, st,
             md ? "MD磁铁OK " : "MD无磁铁 ",
             ml ? "ML太弱 " : "",
             mh ? "MH太强" : "",
             (uint8_t)agc, mag);
    Serial.println(buf);
    delay(300);
}
