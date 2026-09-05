# 04 SimpleFOC — 电机与控制环（BLDCMotor / BLDCDriver3PWM）

> 类定义：`lib/Simple FOC/src/BLDCMotor.h`、`drivers/BLDCDriver3PWM.h`（本地可查）
> 电源到货后闭环验收直接对照本文件。

---

## 对象搭建（四步，顺序固定）

```cpp
#include <SimpleFOC.h>

// 1. 电机对象：参数 = 极对数（C2208-100T = 7）
BLDCMotor motor = BLDCMotor(7);

// 2. 驱动对象：三个 PWM 相线 + 使能脚（Shield V3.2 实际接线）
BLDCDriver3PWM driver = BLDCDriver3PWM(PA8, PA9, PA10, PB0);

// 3. 传感器对象（闭环必需）
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);

void setup() {
    Serial.begin(115200);

    // 4. 供电电压声明（驱动器据此算占空比）
    driver.voltage_power_supply = 12;
    driver.voltage_limit = 8;          // 限制实际输出到电机的电压（保护用）
    driver.init();
    motor.linkDriver(&driver);         // 把驱动交给电机

    sensor.init();
    motor.linkSensor(&sensor);         // 闭环关键：把编码器交给电机

    motor.voltage_limit = 6;           // 电机侧电压上限（V）
    motor.voltage_sensor_align = 3;    // 对齐时用的电压（大电机 3V 足够）
    motor.init();                      // 初始化
    motor.initFOC();                   // ★ 电角度对齐——上电轴会轻动一下，正常
}
```

### `motor.initFOC()`
- 作用：电角度零点标定（给固定电压让转子对齐 → 读编码器 → 算出 `zero_electric_angle`）
- **坑**：无 12V 供电时对齐失败，闭环直接瘫痪（这正是当时 gimbal 固件 Now 恒 0 的深层原因之一）。对齐方向随机时下节参数手动指定

---

## 主循环（闭环心跳，不能阻塞）

```cpp
void loop() {
    motor.loopFOC();     // 电流环：FOC 换相。要求 >1kHz 调用率，循环里不能有 delay
    motor.move(target);  // 控制环：按 motor.controller 模式趋近 target
}
```
- `loopFOC()` 同时自动 pull 传感器（所以闭环固件不用手动 `sensor.update()`）
- `move(target)` 的 target 含义取决于控制模式：电压 / rad/s / rad

---

## 三种控制模式（`motor.controller`）

```cpp
motor.controller = MotionControlType::torque;   // 力矩(电压)模式：move(±3V)
motor.controller = MotionControlType::velocity; // 速度模式：move(5.0) = 5 rad/s
motor.controller = MotionControlType::angle;    // 位置模式：move(3.14) = 转到 180°
```
- 本项目无电流采样（Shield V3.2 的 ACS712 未接），力矩环只能是 voltage 方式——**够用**，旋钮/云台都是低速场景

## 开环模式（不需要 12V 也能转！）

```cpp
// 不 linkSensor、不调 loopFOC，直接：
motor.velocityOpenloop(5.0f);   // 开环 5 rad/s 转动（main.cpp 验证转动用）
motor.angleOpenloop(3.14f);     // 开环转到指定角
```
- 原理：不管反馈，直接按时间强制换相。**电源到货第一件事就是烧 openloop 验证驱动板和电机是好的**

---

## 关键参数调优表（闭环手感全在这几行）

| 参数 | 作用 | 起始值 | 调节方向 |
|---|---|---|---|
| `motor.PID_velocity.P` | 速度环比例 | 0.2 | 电机嗡嗡震荡→减小；软绵跟不上→增大 |
| `motor.PID_velocity.I` | 速度环积分 | 20 | 消除稳态误差；太大会低频摆动 |
| `motor.PID_velocity.output_ramp` | 输出斜率限制 | 1000 | 越小越温柔 |
| `motor.LPF_velocity.Tf` | 速度低通时间常数 | 0.01 | 读数抖→增大；响应迟钝→减小 |
| `motor.P_angle.P` | 位置环比例 | 20 | 位置模式刚度；太大震颤 |
| `motor.velocity_limit` | 位置模式最大速度 | 20 rad/s | — |

## 跳过上电对齐（固化标定结果）

```cpp
motor.sensor_direction = Direction::CW;   // 对齐日志里抄下来
motor.zero_electric_angle = 2.34;         // 对齐日志里抄下来
motor.initFOC();                          // 现在不再动轴，直接就绪
```
- 用途：正式固件不想让上电时轴抖一下；或对齐偶发失败时锁定结果

---

## 完整最小示例（电压模式闭环，等电源到货验证用）

```cpp
#include <SimpleFOC.h>
BLDCMotor motor = BLDCMotor(7);
BLDCDriver3PWM driver = BLDCDriver3PWM(PA8, PA9, PA10, PB0);
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
Commander command = Commander(Serial);              // 串口命令解析器

void doTarget(char* cmd) { command.scalar(&motor.target, cmd); }   // "T 5" → 设目标

void setup() {
    Serial.begin(115200);
    driver.voltage_power_supply = 12;
    driver.init();
    motor.linkDriver(&driver);
    sensor.init();
    motor.linkSensor(&sensor);
    motor.voltage_limit = 6;
    motor.voltage_sensor_align = 3;
    motor.controller = MotionControlType::velocity;
    motor.PID_velocity.P = 0.2f;
    motor.PID_velocity.I = 20.0f;
    motor.LPF_velocity.Tf = 0.01f;
    motor.init();
    motor.initFOC();
    command.add('T', doTarget, "target rad/s");     // 注册命令
    Serial.println("Ready. T<速度> 控制，如 T 5");
}

void loop() {
    motor.loopFOC();         // 电流环（自动拉取传感器，含开环模式）
    motor.move(motor.target);// 按目标速度转
    command.run();           // 处理串口命令（注意：2.3.4 没有 motor.command()）
}
```
- 验收顺序：电压模式微动 → 速度模式 ±5 rad/s → 位置模式 0↔180° → 8 档棘轮（社区公式见 06 文件）
