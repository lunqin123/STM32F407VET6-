# 01 Arduino 基础函数

> stm32duino 核心。这几个函数贯穿所有固件，30 分钟读完。

---

## Serial（USB CDC 串口）

### `Serial.begin(baud)`
- 作用：初始化串口。**F407 上 Serial = USB CDC**，走 PA11/PA12 的板载 USB 口
- 示例：
```cpp
Serial.begin(115200);   // 波特率虚设——USB 是全速数字链路
delay(1000);            // 给电脑枚举时间，否则开头几条打印会丢
```
- **坑**：① 电脑端必须 `ser.setDTR(True)`（DTR 为低时固件不发数据，表现为"连上了但收到 0 字节"）；② ST-Link 不提供串口，不插 USB 线就没有 COM 口；③ 波特率随便填，零字节不要怀疑波特率

### `Serial.print(x)` / `Serial.println(x)`
- 作用：打印。支持 int/float/string，`println` 附加换行
- **坑**：printf 浮点需链接开关 `-Wl,-u,_printf_float`（platformio.ini 已配）。症状：OLED/串口浮点输出为空，只有单位没有数字

### `Serial.available()` / `Serial.read()`
- 作用：查询接收缓冲区字节数 / 读取 1 字节
- 示例（非阻塞读一行命令）：
```cpp
if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();                    // 去掉行尾 \r\n
    if (cmd == "P") toggle_screen();
}
```
- 用途：knob 固件的 P 命令、gimbal 固件的 `T<弧度>` 协议全靠这套

---

## 时间函数

### `millis()` / `micros()`
- 作用：上电以来经过的毫秒/微秒数（uint32_t，约 49.7 天溢出）
- 标准用法——**非阻塞定时**（主循环里永远优先这个，不用 delay）：
```cpp
unsigned long last = 0;
void loop() {
    if (millis() - last > 100) {   // 每 100ms 执行一次
        last = millis();
        do_something();
    }
    // 循环其余部分照常跑，不卡顿
}
```
- **坑**：比较必须用减法 `millis() - last > 100`，不能写 `millis() > last + 100`——前者在溢出时依然正确
- 项目实例：knob.cpp 的 10s 息屏定时、100ms 运动检测窗口全是这个模式

### `delay(ms)`
- 作用：死等。**只在 setup() 初始化阶段用**（等 USB 枚举、等传感器就绪）
- **坑**：主循环里 delay 会卡死 FOC 电流环（loopFOC 要求每秒调用 >1k 次），也会让旋钮响应迟钝

---

## GPIO

### `pinMode(pin, mode)` / `digitalWrite(pin, val)`
- 作用：配置引脚方向（OUTPUT/INPUT/INPUT_PULLUP）/ 写电平
- 示例（板载 LED 心跳，PC13 低电平点亮）：
```cpp
pinMode(PC13, OUTPUT);
digitalWrite(PC13, LOW);    // 亮
digitalWrite(PC13, HIGH);   // 灭
```
- 项目实例：selftest.cpp 上电闪 5 下 = 烧录成功的无串口验证信号

---

## 工具函数

| 函数 | 作用 | 示例 |
|---|---|---|
| `constrain(x, lo, hi)` | 限幅 | `knob_f = constrain(knob_f, 0, 100);` |
| `map(x, in_lo, in_hi, out_lo, out_hi)` | 线性映射（整数版） | `pct = map(raw, 0, 4095, 0, 100);` |
| `fabsf(x)` / `fabs(x)` | 浮点绝对值 | `if (fabsf(d) > 0.01f)` |
| `round(x)` | 四舍五入 | 社区棘轮公式 `round(angle/dist)*dist` |
| `PI` | π 常量 | 角度换算 `d * (UNITS_PER_TURN / (2.0f*PI))` |

- **坑**：角度/位置类累加一定用 `float` 变量，取整只在显示层做——整数中途取整会在慢转时丢步（knob v1 的教训）
