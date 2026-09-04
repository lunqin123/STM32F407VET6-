# STM32F407VET6 引脚分配速查 (Pinout Reference)

> 芯片: STM32F407VET6 — LQFP100 封装 (100 脚)
> 本文件是从 `STM32F407VET6_PeripheralPins.c` (stm32duino GitHub 官方源) 抽取整理的**人读版**。
> 需要逐字节权威数据时，直接看同目录的 `.c` 文件，或数据手册 DocID022152 的 Pinout / Pin-description 表。
> 复用功能编号 (AF) 对应 GPIOx_AFR 寄存器要写的位域。
>
> 约定:
> - 每个外设信号的「默认引脚」列在最前；带 `*` 的是同引脚可复用的其它实例 (靠 `_ALTx` 选择)。
> - ADC 的 INx 通道号同时标出，方便对照 `ADCx->SQR` 配置。
> - 一个物理引脚能接多个外设，靠配置 GPIOx_AFR + 对应外设时钟实现「引脚复用」。

---

## 1. ADC (模数转换) — 12 位, 最多 16 通道 + 内部通道

| 引脚 | ADC1 通道 | 备注 (可复用实例) |
|------|-----------|------------------|
| PA0  | IN0  | 也可做 ADC2_IN0 / ADC3_IN0 (`PA_0_ALT1/2`) |
| PA1  | IN1  | 也可做 ADC2_IN1 / ADC3_IN1 |
| PA2  | IN2  | 也可做 ADC2_IN2 / ADC3_IN2 |
| PA3  | IN3  | 也可做 ADC2_IN3 / ADC3_IN3 |
| PA4  | IN4  | 也可做 ADC2_IN4 (注意 PA4 也是 DAC_OUT1 / SPI1_NSS) |
| PA5  | IN5  | 也可做 ADC2_IN5 (注意 PA5 也是 DAC_OUT2 / SPI1_SCK) |
| PA6  | IN6  | 也可做 ADC2_IN6 |
| PA7  | IN7  | 也可做 ADC2_IN7 |
| PB0  | IN8  | 也可做 ADC2_IN8 |
| PB1  | IN9  | 也可做 ADC2_IN9 |
| PC0  | IN10 | 也可做 ADC2_IN10 / ADC3_IN10 |
| PC1  | IN11 | 也可做 ADC2_IN11 / ADC3_IN11 |
| PC2  | IN12 | 也可做 ADC2_IN12 / ADC3_IN12 |
| PC3  | IN13 | 也可做 ADC2_IN13 / ADC3_IN13 |
| PC4  | IN14 | 也可做 ADC2_IN14 |
| PC5  | IN15 | 也可做 ADC2_IN15 |

> 结论: 模拟输入随手用 **PA0–PA7 → IN0–IN7**、**PB0–PB1 → IN8–IN9**、**PC0–PC5 → IN10–IN15** 即可。
> ADC2/ADC3 仅部分引脚可用 (见 `.c` 中 `_ALT` 行)。

---

## 2. USART / UART (串口) — 波特率由 `USARTx_BRR` 决定, 可任意自定义

| 外设 | TX (发送) | RX (接收) | 复用 AF | 备注 |
|------|-----------|-----------|---------|------|
| USART1 | PA9 / PB6 | PA10 / PB7 | AF7 | 最常用, 接 USB-TTL 调试 |
| USART2 | PA2 / PD5 | PA3 / PD6 | AF7 |  |
| USART3 | PB10 / PD8 / PC10 | PB11 / PD9 / PC11 | AF7 |  |
| USART6 | PC6 | PC7 | AF8 |  |
| UART4  | PA0 / PC10 | PA1 / PC11 | AF8 | 仅 TX/RX, 无流控 |
| UART5  | PC12 | PD2 | AF8 | 仅 TX/RX, 无流控 |

> 经典接法: USB-TTL 的 TX→PA10(RX)、RX→PA9(TX), 波特率写 `USART1_BRR`, 例: 42MHz PCLK / 115200 ≈ 0x16C (查表得 0x16C)。
> 流控 (RTS/CTS) 引脚见 `.c` 中 `PinMap_UART_RTS / PinMap_UART_CTS`。

---

## 3. SPI (同步串行) — AF5 (SPI1/2) / AF6 (SPI3)

| 信号 | SPI1 | SPI2 | SPI3 |
|------|------|------|------|
| MOSI | PA7 / PB5 | PB15 / PC3 | PB5 / PB15 / PC12 |
| MISO | PA6 / PB4 | PB14 / PC2 | PB4 / PC11 |
| SCLK | PA5 / PB3 | PB10 / PB13 | PB3 / PC10 |
| SSEL (NSS) | PA4 / PA15 | PB9 / PB12 | PA4 / PA15 |

> 主从模式+片选都靠 AF 配置; SPI1 常用 PA5/PA6/PA7 + PA4(NSS)。

---

## 4. I2C (两线总线) — AF4, 开漏 (OD)

| 外设 | SDA | SCL |
|------|-----|-----|
| I2C1 | PB7 / PB9 | PB6 / PB8 |
| I2C2 | PB11 | PB10 |
| I2C3 | PC9 | PA8 |

---

## 5. CAN (控制器局域网) — AF9

| 外设 | TD (发送 TX) | RD (接收 RX) |
|------|--------------|--------------|
| CAN1 | PA12 / PB9 / PD1 | PA11 / PB8 / PD0 |
| CAN2 | PB13 / PB6 | PB12 / PB5 |

> 最常见接法: **CAN1 用 PA12(TD)+PA11(RD)**, 外接 CAN 收发器 (如 TJA1050) 后连到 CANH/CANL。
> CAN2 是「从」控制器, 引脚在 PB 上, 注意 CAN1/2 共用 512 字节 SRAM 收发包 FIFO。

---

## 6. TIM / PWM (定时器输出比较) — 能产生 PWM 的通道

| 定时器 | 可用 PWM 通道引脚 (CHx / CHxN 互补) | 复用 AF |
|--------|-------------------------------------|---------|
| TIM1 (高级) | PA8(CH1) PA9(CH2) PA10(CH3) PA11(CH4) PB0(CH2N) PB1(CH3N) PB13(CH1N) PB14(CH2N) PB15(CH3N) PE9/11/13/14(CH1–4) PE8/10/12(CH1N–3N) | AF1 |
| TIM2 | PA0 PA1 PA2 PA3 PA5 PA15 PB3 PB10 PB11 (CH1–4) | AF1 |
| TIM3 | PA6 PA7 PB0 PB1 PB4 PB5 PC6 PC7 PC8 PC9 (CH1–4) | AF2 |
| TIM4 | PB6 PB7 PB8 PB9 PD12 PD13 PD14 PD15 (CH1–4) | AF2 |
| TIM5 | PA0 PA1 PA2 PA3 (CH1–4) | AF2 |
| TIM8 (高级) | PC6 PC7 PC8 PC9 (CH1–4) PA5(CH1N) PB0/1(CH2/3N) PB14/15(CH2/3N) | AF3 |
| TIM9  | PA2 PA3 PE5 PE6 (CH1/2) | AF3 |
| TIM10 | PB8 (CH1) | AF3 |
| TIM11 | PB9 (CH1) | AF3 |
| TIM12 | PB14 PB15 (CH1/2) | AF9 |
| TIM13 | PA6 (CH1) | AF9 |
| TIM14 | PA7 (CH1) | AF9 |

> PWM 三步: 1) 开对应 GPIO 时钟 + 配 AFR 为表中 AF; 2) 开 TIM 时钟, 设 `PSC`(预分频)+`ARR`(自动重装) 定频率, `CCRx` 定占空比; 3) `CCMRx` 设 PWM 模式 + `CCER` 使能 + `CR1_CEN` 启动。
> 高级定时器 TIM1/8 还需置 `BDTR_MOE` 主输出使能, 互补通道 (N) 才能出波。

---

## 7. 其它常用外设引脚 (摘要)

- **DAC**: PA4 (DAC_OUT1), PA5 (DAC_OUT2)
- **USB_OTG_FS**: PA11(DM) PA12(DP) PA9(VBUS) PA10(ID) PA8(SOF) — AF10
- **SDIO**: PC8–11(D0–3) PC12(CK) PD2(CMD) — AF12 (4-bit 模式还要 PB8/9 做 D4/5, AF12)
- **ETH (RMII)**: PA1(REF_CLK) PA2(MDIO) PC1(MDC) PB11(TX_EN) PB12/13(TXD0/1) PC4/5(RXD0/1) PA7(CRS_DV) — AF11

> 完整以太网/USB/SD 引脚见同目录 `STM32F407VET6_PeripheralPins.c`。

---

## 8. 怎么用这些文件回答引脚问题

- 问「XX 引脚能干什么」→ 在 `STM32F407VET6_PeripheralPins.c` 里 `grep -n "PA_11"` 即可看到它挂在 CAN1/CAN2/TIM1/USB 上。
- 问「CAN 用哪几个脚」→ 搜 `PinMap_CAN_TD` / `PinMap_CAN_RD`。
- 问「某个 AF 编号对应什么」→ AF 编号写在每行 `GPIO_AFx_xxx`, 与 RM0090 的 GPIO 复用表一致。
- 数据手册 (PDF) 的引脚定义表是最高权威, 本文件是其「外设→引脚」结构化抽取; SVD 文件只管寄存器地址, 不管引脚。
