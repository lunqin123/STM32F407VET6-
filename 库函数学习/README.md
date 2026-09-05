# 库函数学习手册

> 本手册只收录**本项目将来会用到**的函数，全部 API 已对照本地库源码核实
> （SimpleFOC 2.3.4 / U8g2 2.36.18 / stm32duino Arduino 核心）。
> 每个函数按统一格式整理：作用 → 签名 → 最小示例 → 实测踩坑。

## 文件索引与阅读顺序

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| [01-Arduino基础.md](01-Arduino基础.md) | Serial / millis / GPIO / 工具函数 | 现在读，30 分钟 |
| [02-Wire-I2C.md](02-Wire-I2C.md) | I2C 总线直读（AS5600 寄存器级） | 排查传感器问题时 |
| [03-SimpleFOC-传感器.md](03-SimpleFOC-传感器.md) | MagneticSensorI2C 全部方法 | 做闭环前必读 |
| [04-SimpleFOC-电机.md](04-SimpleFOC-电机.md) | BLDCMotor / Driver / 控制模式 / PID | 电源到货前必读 |
| [05-U8g2-OLED.md](05-U8g2-OLED.md) | OLED 绘图全函数 + 字体表 | 写界面随手查 |
| [06-将来扩展.md](06-将来扩展.md) | 两轴 / 换编码器 / Commander 调试 | P2 阶段再看 |

## 学习方法（配合本手册）

1. **每个函数都配了可编译示例**——放进 `firmware-pio/src/` 新建 `[env:练习名]` 环境就能烧录验证
2. 读到"坑"字样的是本项目实机踩过的，优先记住
3. 手册查不到的，走检索漏斗：examples 全局搜索 → 头文件 → 官方文档 → 最小程序实测

## 本项目硬件速记

```
F407VET6 + SimpleFOC Shield V3.2 (DRV8313)
电机: C2208-100T  120KV / 7极对 / 相电阻21.2Ω
编码器: AS5600 @ I2C 0x36 (SDA=PB7, SCL=PB6)
OLED: SSD1306 @ I2C 0x3C (同一条总线)
PWM: PA8/PA9/PA10 (TIM1)   Enable: PB0
LED: PC13    USB CDC: PA11/PA12 (Serial 即 USB)
```
