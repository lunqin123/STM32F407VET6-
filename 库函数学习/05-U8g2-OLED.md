# 05 U8g2 — OLED 绘图（SSD1306 128×64）

> 类定义：`lib/U8g2/src/U8g2lib.h`（本地可查）
> 用法固定三步：clearBuffer → 画 → sendBuffer。所有坐标以左上角为原点。

---

### 构造与初始化
```cpp
#include <U8g2lib.h>
// F = 全帧缓冲(1KB RAM, 支持任意绘制顺序) / HW_I2C = 硬件 I2C (PB6/PB7)
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

void setup() {
    u8g2.begin();
}
```
- `U8G2_R0` = 不旋转屏幕；`U8X8_PIN_NONE` = 无复位脚（我们的模块接法）

---

## 常用绘制函数

### `u8g2.clearBuffer()` / `u8g2.sendBuffer()`
- 作用：清空内存画布 / 一次性推到屏幕。**每帧开头结尾固定一对**
- **坑**：必须成对出现，sendBuffer 才是真正上屏

### `u8g2.setFont(u8g2_font_...)` + `u8g2.drawStr(x, y, str)`
- 作用：设字体后画字符串。**y 是文字基线（底部）**，不是顶部
- 示例：
```cpp
u8g2.setFont(u8g2_font_10x20_tn);        // 大数字字体
char buf[16];
snprintf(buf, sizeof(buf), "%d", knob);  // ★ 整数用 %d；%f 已被修复可用但要谨慎
u8g2.drawStr(64, 40, buf);               // 居中偏上
```
- **坑**：格式化缓冲用 `snprintf` 而非 `sprintf`，防止溢出；字符串内容用 `char buf[]` 中转

### 常用字体表（够用就好，别贪多——字体都占 flash）

| 字体名 | 尺寸 | 用途 |
|---|---|---|
| `u8g2_font_6x12_tn` | 6×12 纯数字 | 小数值 |
| `u8g2_font_10x20_tn` | 10×20 纯数字 | 主数值（knob 在用） |
| `u8g2_font_9x15B_tf` | 9×15 含 ASCII | 标签文字 |
| `u8g2_font_unifont_t_symbols` | 16×16 | 符号/箭头 |
- `_tn` = 数字专用（省空间），`_tf` = 全 ASCII，`_t_symbols` = 带符号

### 几何图形
```cpp
u8g2.drawBox(0, 60, knob*128/100, 4);    // 实心矩形：knob 进度条（y=60 高4）
u8g2.drawFrame(x, y, w, h);              // 空心矩形
u8g2.drawDisc(64, 32, 10);               // 实心圆(圆心x,y,半径)
u8g2.drawCircle(64, 32, 10);             // 空心圆
u8g2.drawLine(0, 0, 127, 63);            // 线段
```
- 项目实例（knob.cpp）：顶部 `drawStr` 数值 + 底部 `drawBox` 进度条 + 边界时 `drawDisc` 指示

### `u8g2.getStrWidth(str)`
- 作用：返回当前字体下字符串的像素宽——**居中显示的标准做法**：
```cpp
u8g2.setFont(u8g2_font_10x20_tn);
u8g2.drawStr((128 - u8g2.getStrWidth(buf)) / 2, 40, buf);
```

---

## 坑区：屏幕开关（本项目实盘教训）

### `u8g2.setPowerSave(0/1)` —— 不要用
- 1 = 发 display-off 命令关屏。**但它会冻结整条 I2C 总线**，同总线的 AS5600 一起死，唤醒条件永远不满足
- ✅ 正确做法：**推全黑帧模拟关屏**（总线保持活动）：
```cpp
bool screen_on = true;

void draw() {
    u8g2.clearBuffer();
    if (screen_on) {
        u8g2.setFont(u8g2_font_10x20_tn);
        u8g2.setCursor(30, 40);
        u8g2.print(knob);
        u8g2.drawBox(0, 60, knob * 128 / 100, 4);
    }
    u8g2.sendBuffer();      // screen_on=false 时推的就是全黑帧
}
```

---

## 完整最小示例（无电机，显示 AS5600 角度盘）

```cpp
#include <U8g2lib.h>
#include <SimpleFOC.h>

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);

void setup() {
    Serial.begin(115200);
    u8g2.begin();
    sensor.init();
}

void loop() {
    sensor.update();                              // pull 模型
    float deg = sensor.getMechanicalAngle() * 57.2958f;

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_10x20_tn);
    char buf[12];
    snprintf(buf, sizeof(buf), "%.0f", deg);
    u8g2.drawStr((128 - u8g2.getStrWidth(buf)) / 2, 40, buf);
    u8g2.drawBox(0, 60, (uint8_t)(deg * 128 / 360), 4);   // 角度进度条
    u8g2.sendBuffer();
    delay(50);
}
```
