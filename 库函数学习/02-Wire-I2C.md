# 02 Wire — I2C 总线直读

> 用途：绕过一切库直读芯片寄存器，是排查传感器问题的"显微镜"。
> `as5600test.cpp` 环境就是这套函数写成的。

---

## 接线前提（本项目）

- F407 的 I2C1：**PB7 = SDA，PB6 = SCL**（与 UNO 相反，方向别记错）
- AS5600 地址固定 `0x36`，OLED `0x3C`，共用一条总线
- 两个器件都只能接 3.3V

---

### `Wire.begin()`
- 作用：初始化 I2C 主机（默认 100kHz）
- 示例：
```cpp
Wire.setClock(400000);   // 可选：提到 400kHz 快速模式，读角度更快
Wire.begin();
```

### `Wire.beginTransmission(addr)` → `Wire.write(reg)` → `Wire.endTransmission()`
- 作用：向某地址芯片写数据（通常是"我要读寄存器 XX"的指针设定）
- 返回值（`endTransmission()`）：

| 返回 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 地址 NACK（芯片不在总线上/接错线） |
| 3 | 数据 NACK |
| 4 | 其他错误 |

- 用途：**扫描总线上有哪些设备**（selftest.cpp 的 scanI2C 核心）：
```cpp
for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
        Serial.print("found: 0x");
        Serial.println(addr, HEX);   // 期望输出 0x36 和 0x3C
    }
}
```

### `Wire.requestFrom(addr, n)` + `Wire.read()`
- 作用：从芯片读 n 个字节，逐个 `read()` 取出
- 完整示例——**直读 AS5600 原始角度**（12 位，寄存器 0x0C/0x0D）：
```cpp
uint16_t readAS5600Raw() {
    Wire.beginTransmission(0x36);
    Wire.write(0x0C);                    // RAW ANGLE 高字节寄存器
    Wire.endTransmission(false);         // false = 不释放总线（重复起始位）
    Wire.requestFrom(0x36, 2);           // 连读 2 字节
    uint16_t raw = ((uint16_t)Wire.read() << 8) | Wire.read();
    return raw;                          // 0~4095 对应 0~360°
}

void setup() {
    Serial.begin(115200);
    Wire.begin();
}
void loop() {
    uint16_t raw = readAS5600Raw();
    Serial.print("RAW="); Serial.print(raw);
    Serial.print("  deg="); Serial.println(raw * 360.0 / 4096.0);
    delay(200);
}
```

---

## 诊断寄存器速查（AS5600）

| 寄存器 | 含义 | 正常值（本机实测） |
|---|---|---|
| 0x0B | STATUS：MD/ML/MH 磁铁状态位 | MD=1（磁铁正确对准） |
| 0x1A | AGC 自动增益 | 0~255，**中间偏小为佳**；过大=磁场太弱 |
| 0x1B/0x1C | MAGNITUDE 磁场强度 | 2137（本机） |
| 0x0C/0x0D | RAW ANGLE 原始角 | 转轴时连续变化 |

- 判断口诀：**RAW 会变=机械/磁铁层 OK；AGC 居中=磁场强度 OK**。这两项正常而 SimpleFOC 读数不对，问题必然在软件层

## 本总线的历史坑

1. **U8g2 `setPowerSave(1)` 会冻结整条 I2C 总线**——display-off 后同总线的 AS5600 也读不动了。结论：不要用 setPowerSave 关屏，改推全黑帧（见 05 文件）
2. OLED 模块若从 Shield 编码器口取电（5V），会把 5V 灌进 F407 的 IO——**OLED/AS5600 永远接 F407 的 3.3V**
