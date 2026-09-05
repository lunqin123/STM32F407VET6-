# 03 SimpleFOC — 传感器（MagneticSensorI2C）

> 类定义：`lib/Simple FOC/src/sensors/MagneticSensorI2C.h`（本地可查）
> 本项目用 AS5600 磁编码器测电机轴角度，是闭环的"眼睛"。

---

### `MagneticSensorI2C(AS5600_I2C)` — 构造
- 作用：创建磁编码器对象。`AS5600_I2C` 是库内置的预定义配置（地址 0x36、12 位、寄存器 0x0C）
- 通用构造（换别的 I2C 编码器时用）：
```cpp
MagneticSensorI2C(uint8_t chip_address, int bit_resolution,
                  uint8_t angle_register_msb, int msb_bits_used);
```
- 项目实例：
```cpp
#include <SimpleFOC.h>
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
```

### `sensor.init(TwoWire* wire = &Wire)`
- 作用：初始化（默认走 Wire = PB6/PB7 这条 I2C1）。第二块编码器可传 `&Wire1`（如果引出并配置了第二路 I2C）
- 示例：`sensor.init();   // 在 Serial.begin() 之后、motor.init() 之前`

---

## ★ 核心：pull 拉取模型（本项目最大教训）

> 所有 getter 返回的是**上次 update() 缓存的值**，不读芯片！

- `motor.loopFOC()` 第一行就调 `sensor->update()` → **含电机的固件不用手动调**。源码（BLDCMotor.cpp）确认：**开环模式也会更新传感器**（官方注释：防止用户中途切模式丢失多圈计数）
- **不含 loopFOC 的固件（纯传感器应用，如 knob、角度面板）必须每帧自己调**：
```cpp
void loop() {
    sensor.update();                        // ← 漏了这行，读数永远是上电初值
    float a = sensor.getMechanicalAngle();  // 现在才是真值
}
```

---

## 四个角度函数辨析（长得像，含义完全不同）

| 函数 | 返回 | 范围 | 什么时候用 |
|---|---|---|---|
| `getSensorAngle()` | 原始传感器角 | 0~2π | 全圈显示、底层诊断 |
| `getMechanicalAngle()` | 单圈机械角 | 0~2π wrap | 旋钮显示、单圈位置 |
| `getAngle()` | **连续多圈累计角** | -∞~+∞ | 云台转了多少圈、需多圈定位时 |
| `getVelocity()` | 角速度 | rad/s | 速度环反馈、甩动检测 |

- **坑**：用 `getAngle()` 做旋钮显示，轴来回转数字却一直涨——它不会归零，单圈应用必须用 `getMechanicalAngle()`
- 跨 0/2π 边界算增量要处理回卷：
```cpp
float d = raw - last_raw;
if (d > PI)  d -= 2*PI;
if (d < -PI) d += 2*PI;    // 现在 d 是真实的本帧增量
```

### 单位换算
- SimpleFOC 全部用**弧度**。换算：`deg = rad * 57.2958`；`rad = deg * 0.0174533`
- 项目实例（knob.cpp）：`knob_f += d * (UNITS_PER_TURN / (2.0f*PI))` —— 每圈 2π 弧度换算成 50 格

---

## 完整最小示例（纯传感器，无电机）

```cpp
#include <SimpleFOC.h>

MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
unsigned long t0 = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);
    sensor.init();
}

void loop() {
    sensor.update();                       // pull 模型：必须自己拉
    Serial.print("mech(deg)=");  Serial.print(sensor.getMechanicalAngle() * 57.2958f);
    Serial.print("  full(deg)="); Serial.print(sensor.getAngle() * 57.2958f);
    Serial.print("  vel(rad/s)="); Serial.println(sensor.getVelocity());
    delay(100);
}
```
- 编译烧录：`platformio.ini` 加 `[env:sensortest]` + `build_src_filter = +<sensortest.cpp>`
- 观察点：转轴时 mech 在 0~360 循环；full 一直累加；vel 约等于转动速度
