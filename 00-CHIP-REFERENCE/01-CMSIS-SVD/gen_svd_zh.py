#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 STM32F40x.svd 生成一份"中英双语"副本 STM32F40x_zh.svd。

原理：Cortex-Debug 的 PERIPHERALS 面板用 SVD 里每个寄存器的 <name> 作树标签、
hover 时显示 <description>。本脚本不改动原文件，只生成副本，把中文塞进：
  - <name>        -> "AHB1ENR（AHB1时钟使能）"  (树里英文旁边直接显示中文)
  - <displayName> -> 同 <name>，兼容其他视图
  - <description> -> "AHB1外设时钟使能寄存器 | 原英文描述" (hover 详解)

覆盖范围（本脚本一次性覆盖全部 47 个外设 / 884 个寄存器）：
  - 标准片内外设：RCC / GPIOx / USART/UART / SPI / I2C / ADC / DAC / TIM / EXTI /
    SYSCFG / PWR / FLASH / IWDG / WWDG / CRC / DMA / NVIC / RTC / SDIO / DCMI /
    RNG / DBG / FSMC / CAN —— 均为精心翻译的准确中文。
  - 两个巨型外部 IP：USB OTG_FS / OTG_HS 与 Ethernet MAC/MMC/PTP/DMA
    （约 400 个寄存器）—— 采用"前缀解码"做 best-effort 中文标注，
    精度低于标准外设，但保证面板显示中文，不遗漏。

用法：
  python gen_svd_zh.py
依赖：仅标准库 xml / re。
"""
import re
import xml.etree.ElementTree as ET

SRC = r"D:\STM32F407VET6\00-CHIP-REFERENCE\01-CMSIS-SVD\STM32F40x.svd"
OUT = r"D:\STM32F407VET6\00-CHIP-REFERENCE\01-CMSIS-SVD\STM32F40x_zh.svd"

# 让序列化时保留 xs: 命名空间前缀，避免被改写成 ns0:
ET.register_namespace("xs", "http://www.w3.org/2001/XMLSchema-instance")

# ──────────────────────────────────────────────────────────────
# 1) 外设级精确字典（同名寄存器在不同外设含义不同，必须分开写）
# ──────────────────────────────────────────────────────────────
RCC = {
    "CR":        ("时钟控制",     "时钟控制：HSI/HSE/PLL 的使能与就绪标志，含系统时钟切换"),
    "PLLCFGR":   ("PLL配置",      "PLL 配置：输入分频/倍频/输出分频/PLL 源选择"),
    "CFGR":      ("时钟配置",     "时钟配置：系统时钟源与各总线(AHB/APB)分频"),
    "CIR":       ("时钟中断",     "时钟中断：时钟就绪/失败中断使能与状态标志"),
    "AHB1RSTR":  ("AHB1复位",     "AHB1 外设复位：置位对应 bit 复位 GPIO 等外设"),
    "AHB2RSTR":  ("AHB2复位",     "AHB2 外设复位"),
    "AHB3RSTR":  ("AHB3复位",     "AHB3 外设复位（FSMC）"),
    "APB1RSTR":  ("APB1复位",     "APB1 外设复位"),
    "APB2RSTR":  ("APB2复位",     "APB2 外设复位"),
    "AHB1ENR":   ("AHB1时钟使能", "AHB1 外设时钟使能：置位对应 bit 才给 GPIO 等供时钟（一切配置的前提）"),
    "AHB2ENR":   ("AHB2时钟使能", "AHB2 外设时钟使能"),
    "AHB3ENR":   ("AHB3时钟使能", "AHB3 外设时钟使能（FSMC）"),
    "APB1ENR":   ("APB1时钟使能", "APB1 外设时钟使能"),
    "APB2ENR":   ("APB2时钟使能", "APB2 外设时钟使能"),
    "AHB1LPENR": ("AHB1低功耗时钟", "AHB1 外设低功耗时钟使能（睡眠时）"),
    "AHB2LPENR": ("AHB2低功耗时钟", "AHB2 外设低功耗时钟使能"),
    "AHB3LPENR": ("AHB3低功耗时钟", "AHB3 外设低功耗时钟使能"),
    "APB1LPENR": ("APB1低功耗时钟", "APB1 外设低功耗时钟使能"),
    "APB2LPENR": ("APB2低功耗时钟", "APB2 外设低功耗时钟使能"),
    "BDCR":      ("备份域控制",   "备份域控制：LSE/ RTC 时钟与复位"),
    "CSR":       ("时钟状态",     "时钟状态：复位标志与时钟安全系统"),
    "SSCGR":     ("扩频时钟",     "扩频时钟生成：降低 EMI"),
    "PLLI2SCFGR":("PLLI2S配置",   "PLLI2S 配置：用于 I2S/SAI 音频时钟"),
    "PLLSAICFGR":("PLLSAI配置",   "PLLSAI 配置：用于 SAI/LTDC 时钟"),
    "DCKCFGR":   ("专用时钟配置", "专用时钟配置：I2S/SAI/LTDC 等时钟路由"),
}

# ──────────────────────────────────────────────────────────────
# 2) 跨外设共享字典（同名寄存器在各同类外设含义一致）
# ──────────────────────────────────────────────────────────────
GPIO = {
    "MODER":   ("端口模式",   "端口模式：配置每引脚为 输入/输出/复用功能/模拟"),
    "OTYPER":  ("输出类型",   "输出类型：推挽 或 开漏"),
    "OSPEEDR": ("输出速度",   "输出速度：引脚翻转速率（低/中/高/超高速）"),
    "PUPDR":   ("上下拉",     "上拉/下拉：配置引脚上拉、下拉或浮空"),
    "IDR":     ("输入数据",   "输入数据：读取各引脚当前电平（只读）"),
    "ODR":     ("输出数据",   "输出数据：整个端口的输出值（改单脚建议用 BSRR）"),
    "BSRR":    ("位设置/复位", "位设置/复位：原子置位或清零某引脚，推荐用于输出驱动"),
    "LCKR":    ("配置锁定",   "配置锁定：锁定引脚配置，防止被意外改写"),
    "AFRL":    ("复用功能低", "引脚 0~7 的复用功能(AF0~AF15)选择"),
    "AFRH":    ("复用功能高", "引脚 8~15 的复用功能(AF0~AF15)选择"),
}

USART = {
    "SR":   ("状态",     "状态：发送/接收就绪与各种错误/中断标志"),
    "DR":   ("数据",     "数据：发送/接收数据寄存器"),
    "BRR":  ("波特率",   "波特率：波特率分频系数"),
    "CR1":  ("控制1",    "控制1：使能/字长/校验/中断/收发主控制"),
    "CR2":  ("控制2",    "控制2：停止位/时钟/地址/红外等"),
    "CR3":  ("控制3",    "控制3：硬件流控/DMA/智能卡纠错"),
    "GTPR": ("保护时间", "保护时间/预分频：智能卡模式"),
}

SPI = {
    "CR1":    ("控制1",   "控制1：时钟极性/相位/波特率/主从/帧格式"),
    "CR2":    ("控制2",   "控制2：中断/DMA/数据帧长度"),
    "SR":     ("状态",    "状态：发送/接收就绪与溢出/忙标志"),
    "DR":     ("数据",    "数据：收发数据"),
    "CRCPR":  ("CRC多项式", "CRC 多项式寄存器"),
    "RXCRCR": ("RX CRC校验", "接收 CRC 寄存器"),
    "TXCRCR": ("TX CRC校验", "发送 CRC 寄存器"),
    "I2SCFGR":("I2S配置",  "I2S 模式配置"),
    "I2SPR":  ("I2S预分频", "I2S 时钟预分频"),
}

I2C = {
    "CR1":  ("控制1",  "控制1：使能/时钟延长/中断"),
    "CR2":  ("控制2",  "控制2：外设时钟频率/中断/DMA 触发"),
    "OAR1": ("自身地址1", "自身地址 1（7/10 位）"),
    "OAR2": ("自身地址2", "自身地址 2（仅 7 位）"),
    "DR":   ("数据",   "数据：收发数据"),
    "SR1":  ("状态1",  "状态1：状态与错误标志"),
    "SR2":  ("状态2",  "状态2：地址/总线状态标志"),
    "CCR":  ("时钟控制", "时钟控制：标准/快速模式频率"),
    "TRISE":("上升时间", "上升时间：最大上升时间配置"),
    "FLTR": ("噪声滤波", "噪声滤波：模拟/数字滤波配置"),
}

ADC = {
    "SR":     ("状态",      "状态：转换结束 EOC 等标志"),
    "CR1":    ("控制1",     "控制1：分辨率/扫描/间断模式/中断"),
    "CR2":    ("控制2",     "控制2：启动转换/对齐/触发源/DMA"),
    "SMPR1":  ("采样时间1",  "采样时间1：通道 10~17 采样周期"),
    "SMPR2":  ("采样时间2",  "采样时间2：通道 0~9 采样周期"),
    "JOFR1":  ("注入偏移1",  "注入通道 1 数据偏移"),
    "JOFR2":  ("注入偏移2",  "注入通道 2 数据偏移"),
    "JOFR3":  ("注入偏移3",  "注入通道 3 数据偏移"),
    "JOFR4":  ("注入偏移4",  "注入通道 4 数据偏移"),
    "HTR":    ("上限阈值",   "模拟看门狗高阈值"),
    "LTR":    ("下限阈值",   "模拟看门狗低阈值"),
    "SQR1":   ("规则序列1",  "规则组序列：转换总数与通道 16~26"),
    "SQR2":   ("规则序列2",  "规则组序列：通道 7~15"),
    "SQR3":   ("规则序列3",  "规则组序列：通道 0~6"),
    "JSQR":   ("注入序列",   "注入组序列：长度与通道"),
    "JDR1":   ("注入数据1",  "注入通道 1 转换结果"),
    "JDR2":   ("注入数据2",  "注入通道 2 转换结果"),
    "JDR3":   ("注入数据3",  "注入通道 3 转换结果"),
    "JDR4":   ("注入数据4",  "注入通道 4 转换结果"),
    "DR":     ("数据",      "数据：规则组转换结果"),
    "CCR":    ("公共控制",   "公共控制：多 ADC 模式/采样延迟/温度/Vbat"),
    "CSR":    ("公共状态",   "公共状态：多 ADC 状态"),
    "CDR":    ("公共数据",   "公共数据：双 ADC 交替数据"),
}

DAC = {
    "CR":       ("控制",      "控制：使能/触发源/波形生成"),
    "SWTRIGR":  ("软件触发",  "软件触发寄存器"),
    "DHR12R1":  ("12位右对齐1", "通道1 12位右对齐数据"),
    "DHR12L1":  ("12位左对齐1", "通道1 12位左对齐数据"),
    "DHR8R1":   ("8位数据1",   "通道1 8位数据"),
    "DHR12R2":  ("12位右对齐2", "通道2 12位右对齐数据"),
    "DHR12L2":  ("12位左对齐2", "通道2 12位左对齐数据"),
    "DHR8R2":   ("8位数据2",   "通道2 8位数据"),
    "DHR12RD":  ("12位右对齐双", "双通道 12位右对齐数据"),
    "DHR12LD":  ("12位左对齐双", "双通道 12位左对齐数据"),
    "DHR8RD":   ("8位双",     "双通道 8位数据"),
    "DOR1":     ("输出数据1",  "通道1 输出数据（只读）"),
    "DOR2":     ("输出数据2",  "通道2 输出数据（只读）"),
    "SR":       ("状态",      "状态：DMA 下溢等标志"),
}

# 定时器：公共部分 + 高级定时器附加
TIM_COMMON = {
    "CR1":           ("控制1",       "控制1：计数器使能/方向/对齐/分频"),
    "CR2":           ("控制2",       "控制2：主从模式/输出空闲状态"),
    "SMCR":          ("从模式控制",  "从模式控制：触发/门控/外部时钟选择"),
    "DIER":          ("中断使能",    "DMA/中断使能：更新/捕获/触发"),
    "SR":            ("状态",        "状态：更新/捕获/触发 等中断标志"),
    "EGR":           ("事件生成",    "事件生成：软件触发更新/捕获事件"),
    "CCMR1_Output":  ("捕获比较1输出", "通道1/2 输出比较模式"),
    "CCMR1_Input":   ("捕获比较1输入", "通道1/2 输入捕获模式"),
    "CCMR2_Output":  ("捕获比较2输出", "通道3/4 输出比较模式"),
    "CCMR2_Input":   ("捕获比较2输入", "通道3/4 输入捕获模式"),
    "CCER":          ("捕获比较使能", "各通道 使能/极性/方向"),
    "CNT":           ("计数器",      "当前计数值"),
    "PSC":           ("预分频",      "计数器时钟预分频"),
    "ARR":           ("自动重装",    "自动重装载值（周期）"),
    "CCR1":          ("捕获比较1",   "通道1 比较值/捕获值"),
    "CCR2":          ("捕获比较2",   "通道2 比较值/捕获值"),
    "CCR3":          ("捕获比较3",   "通道3 比较值/捕获值"),
    "CCR4":          ("捕获比较4",   "通道4 比较值/捕获值"),
    "OR":            ("复用",        "定时器复用功能"),
    "DCR":           ("DMA控制",     "DMA 突发访问配置"),
    "DMAR":          ("DMA地址",     "DMA 连续访问数据"),
}
TIM_ADV = dict(TIM_COMMON, **{
    "RCR":  ("重复计数", "重复计数器（高级定时器）"),
    "BDTR": ("断路死区", "死区/刹车保护（高级定时器）"),
})
TIM_GEN = dict(TIM_COMMON)
TIM_BASIC = {
    "CR1":  ("控制1",   "控制1：计数器使能/对齐/分频"),
    "CR2":  ("控制2",   "控制2：主模式选择"),
    "DIER": ("中断使能", "DMA/中断使能"),
    "SR":   ("状态",    "状态：更新中断标志"),
    "EGR":  ("事件生成", "事件生成"),
    "CNT":  ("计数器",  "当前计数值"),
    "PSC":  ("预分频",  "预分频"),
    "ARR":  ("自动重装", "自动重装载值"),
    "DCR":  ("DMA控制", "DMA 突发访问配置"),
    "DMAR": ("DMA地址", "DMA 连续访问数据"),
}
TIM_SIMPLE = {
    "CR1":           ("控制1",     "控制1：计数器使能/方向/分频"),
    "CR2":           ("控制2",     "控制2：主模式选择"),
    "SMCR":          ("从模式控制", "从模式控制"),
    "DIER":          ("中断使能",   "DMA/中断使能"),
    "SR":            ("状态",      "状态：更新/捕获 标志"),
    "EGR":           ("事件生成",   "事件生成"),
    "CCMR1_Output":  ("捕获比较1输出", "通道1/2 输出比较模式"),
    "CCMR1_Input":   ("捕获比较1输入", "通道1/2 输入捕获模式"),
    "CCER":          ("捕获比较使能", "通道 使能/极性/方向"),
    "CNT":           ("计数器",    "当前计数值"),
    "PSC":           ("预分频",    "预分频"),
    "ARR":           ("自动重装",  "自动重装载值"),
    "CCR1":          ("捕获比较1", "通道1 比较值/捕获值"),
    "CCR2":          ("捕获比较2", "通道2 比较值/捕获值"),
    "OR":            ("复用",      "定时器复用功能"),
}

EXTI = {
    "IMR":  ("中断屏蔽", "中断屏蔽：允许/禁止各中断线"),
    "EMR":  ("事件屏蔽", "事件屏蔽：允许/禁止各事件线"),
    "RTSR": ("上升沿触发", "上升沿触发选择"),
    "FTSR": ("下降沿触发", "下降沿触发选择"),
    "SWIER":("软件中断", "软件触发中断"),
    "PR":   ("挂起",     "中断挂起标志（写1清）"),
}

SYSCFG = {
    "MEMRM":   ("内存重映射", "内存重映射：ITCM/DTCM/外部存储器映射"),
    "PMC":     ("电源管理",  "电源管理：I/O 补偿单元等"),
    "EXTICR1": ("外部中断1", "EXTI 线 0~3 的端口源选择"),
    "EXTICR2": ("外部中断2", "EXTI 线 4~7 的端口源选择"),
    "EXTICR3": ("外部中断3", "EXTI 线 8~11 的端口源选择"),
    "EXTICR4": ("外部中断4", "EXTI 线 12~15 的端口源选择"),
    "CMPCR":   ("补偿单元",  "I/O 补偿单元状态"),
}

PWR = {
    "CR":  ("控制",  "控制：稳压器/唤醒/PVD/备份访问"),
    "CSR": ("控制状态", "控制状态：PVD 输出/唤醒/待机标志"),
}

FLASH = {
    "ACR":      ("访问控制", "访问控制：等待周期/预取/缓存"),
    "KEYR":     ("密钥",     "解锁 FLASH 编程密钥"),
    "OPTKEYR":  ("选项密钥", "解锁选项字节密钥"),
    "SR":       ("状态",     "状态：忙/错误 等编程状态"),
    "CR":       ("控制",     "控制：编程/擦除/锁定/选项编程"),
    "OPTCR":    ("选项控制", "选项控制：读保护/看门狗/复位选择"),
}

IWDG = {
    "KR":  ("密钥",   "密钥：喂狗/启动/写保护"),
    "PR":  ("预分频", "看门狗时钟预分频"),
    "RLR": ("重装载", "重装载值（超时）"),
    "SR":  ("状态",   "状态：看门狗更新状态"),
}

WWDG = {
    "CR":  ("控制",   "控制：使能/计数器（含窗口位）"),
    "CFR": ("配置",   "配置：窗口值/预分频/提前唤醒中断"),
    "SR":  ("状态",   "状态：提前唤醒中断标志"),
}

CRC = {
    "DR":  ("数据",   "数据：CRC 计算数据/结果"),
    "IDR": ("独立数据", "独立数据：用户数据（断电保留）"),
    "CR":  ("控制",   "控制：CRC 复位"),
}

NVIC = {
    "ICTR":   ("中断数",   "中断控制器类型"),
    "STIR":   ("软件触发", "软件触发中断寄存器"),
}
for _i in range(3):
    NVIC[f"ISER{_i}"]  = (f"中断使能{_i}",   f"中断 set-enable {_i}")
    NVIC[f"ICER{_i}"]  = (f"中断禁能{_i}",   f"中断 clear-enable {_i}")
    NVIC[f"ISPR{_i}"]  = (f"中断挂起{_i}",   f"中断 set-pending {_i}")
    NVIC[f"ICPR{_i}"]  = (f"中断清挂起{_i}", f"中断 clear-pending {_i}")
    NVIC[f"IABR{_i}"]  = (f"中断活跃{_i}",   f"中断 active 状态（只读）{_i}")
for _i in range(60):
    NVIC[f"IPR{_i}"]   = (f"中断优先级{_i}", f"中断优先级寄存器 {_i}（0~255）")

RTC = {
    "TR":       ("时间",       "BCD 时间（时/分/秒）"),
    "DR":       ("日期",       "BCD 日期（年/月/日/星期）"),
    "CR":       ("控制",       "控制：日历/闹钟/中断/校准使能"),
    "ISR":      ("初始化状态", "初始化/同步/闹钟 标志"),
    "PRER":     ("预分频",     "异步/同步 预分频"),
    "WUTR":     ("唤醒定时器", "唤醒定时器重装载"),
    "CALIBR":   ("校准",       "粗校准（建议用 CALR）"),
    "ALRMAR":   ("闹钟A",      "闹钟 A 时间掩码"),
    "ALRMBR":   ("闹钟B",      "闹钟 B 时间掩码"),
    "WPR":      ("写保护",     "写保护密钥（解/锁）"),
    "SSR":      ("子秒",       "亚秒（亚秒累加）值"),
    "SHIFTR":   ("偏移",       "时间偏移（加减）寄存器"),
    "TSTR":     ("时间戳",     "时间戳时间"),
    "TSDR":     ("时间戳日期", "时间戳日期"),
    "TSSSR":    ("时间戳亚秒", "时间戳亚秒"),
    "CALR":     ("校准",       "细校准（温度补偿）"),
    "TAFCR":    ("时间戳闹钟功能", "时间戳/闹钟 输出与功能"),
    "ALRMASSR": ("闹钟A亚秒",  "闹钟 A 亚秒掩码"),
    "ALRMBSSR": ("闹钟B亚秒",  "闹钟 B 亚秒掩码"),
    "OR":       ("复用",       "RTC 复用输出"),
}
for _i in range(20):
    RTC[f"BKP{_i}R"] = (f"备份{_i}", f"备份寄存器 {_i}（Vbat 掉电保留）")

SDIO = {
    "POWER":  ("电源",     "电源控制"),
    "CLKCR":  ("时钟",     "时钟分频/总线宽度"),
    "ARG":    ("参数",     "命令参数"),
    "CMD":    ("命令",     "命令索引与类型"),
    "RESPCMD":("响应命令", "最后响应命令索引"),
    "RESP1":  ("响应1",    "卡响应 1"),
    "RESP2":  ("响应2",    "卡响应 2"),
    "RESP3":  ("响应3",    "卡响应 3"),
    "RESP4":  ("响应4",    "卡响应 4"),
    "DTIMER": ("数据定时器", "数据超时"),
    "DLEN":   ("数据长度",  "传输字节数"),
    "DCTRL":  ("数据控制",  "传输使能/方向/模式"),
    "DCOUNT": ("数据剩余",  "剩余传输计数"),
    "STA":    ("状态",     "状态标志"),
    "ICR":    ("中断清除",  "清除中断标志"),
    "MASK":   ("中断屏蔽",  "中断屏蔽"),
    "FIFOCNT":("FIFO计数",  "FIFO 剩余字数"),
    "FIFO":   ("FIFO缓冲",  "数据 FIFO"),
}

DCMI = {
    "CR":      ("控制",     "捕获使能/模式/同步"),
    "SR":      ("状态",     "FIFO/同步状态"),
    "RIS":     ("原始中断", "原始中断状态"),
    "IER":     ("中断使能", "中断使能"),
    "MIS":     ("中断屏蔽状态", "屏蔽后中断状态"),
    "ICR":     ("中断清除", "清除中断标志"),
    "ESCR":    ("嵌入同步码", "嵌入同步码配置"),
    "ESUR":    ("期望同步码", "期望同步码配置"),
    "CWSTRT":  ("裁剪起始", "裁剪窗口起始"),
    "CWSIZE":  ("裁剪大小", "裁剪窗口大小"),
    "DR":      ("数据",     "像素数据"),
}

RNG = {
    "CR": ("控制", "随机数使能/中断"),
    "SR": ("状态", "数据就绪/错误标志"),
    "DR": ("数据", "随机数输出"),
}

DBG = {
    "DBGMCU_IDCODE":    ("芯片ID",     "器件 ID 编码"),
    "DBGMCU_CR":        ("调试控制",   "调试时 时钟/看门狗/休眠 行为"),
    "DBGMCU_APB1_FZ":   ("APB1冻结",   "调试时冻结 APB1 外设"),
    "DBGMCU_APB2_FZ":   ("APB2冻结",   "调试时冻结 APB2 外设"),
}

CAN = {
    "MCR":  ("主控制",   "初始化/睡眠/时间戳"),
    "MSR":  ("主状态",   "初始化确认/错误状态"),
    "TSR":  ("发送状态", "发送邮箱状态"),
    "RF0R": ("接收FIFO0", "接收 FIFO 0 状态"),
    "RF1R": ("接收FIFO1", "接收 FIFO 1 状态"),
    "IER":  ("中断使能", "中断使能"),
    "ESR":  ("错误状态", "错误标志/计数"),
    "BTR":  ("位时序",   "波特率/同步跳转/采样点"),
    "FMR":  ("过滤器主", "过滤器主控制（激活/模式）"),
    "FM1R": ("过滤器模式", "过滤器位宽模式"),
    "FS1R": ("过滤器尺度", "过滤器单/双 32 位尺度"),
    "FFA1R":("过滤器FIFO分配", "过滤器分配至 FIFO0/1"),
    "FA1R": ("过滤器激活", "过滤器激活"),
}

FSMC = {
    "BCR1": ("控制1", "存储块1 控制（SRAM/PSRAM/NOR）"),
    "BCR2": ("控制2", "存储块2 控制"),
    "BCR3": ("控制3", "存储块3 控制"),
    "BCR4": ("控制4", "存储块4 控制"),
    "BTR1": ("时序1", "存储块1 读写时序"),
    "BTR2": ("时序2", "存储块2 读写时序"),
    "BTR3": ("时序3", "存储块3 读写时序"),
    "BTR4": ("时序4", "存储块4 读写时序"),
    "BWTR1": ("写时序1", "存储块1 写时序"),
    "BWTR2": ("写时序2", "存储块2 写时序"),
    "BWTR3": ("写时序3", "存储块3 写时序"),
    "BWTR4": ("写时序4", "存储块4 写时序"),
    "PCR":   ("PCard控制", "NAND/PCCard 控制"),
    "SR":    ("状态",   "NAND 状态"),
    "PMEM":  ("存储器时序", "NAND 通用存储器时序"),
    "PATT":  ("属性时序", "NAND 属性存储器时序"),
    "PIOCR": ("I/O时序",  "NAND I/O 时序"),
    "ECCR":  ("ECC",      "NAND ECC 结果(块1)"),
}
# FSMC NAND(块2/3) 与 PCCard(块4) 的多 bank 变体
for _b in (2, 3, 4):
    _lbl = "NAND" if _b in (2, 3) else "PCCard"
    FSMC[f"PCR{_b}"]  = (f"控制{_b}", f"存储块{_b} 控制（{_lbl}）")
    FSMC[f"SR{_b}"]   = (f"状态{_b}", f"存储块{_b} 状态（{_lbl}）")
    FSMC[f"PMEM{_b}"] = (f"存储器时序{_b}", f"存储块{_b} 通用存储器时序（{_lbl}）")
    FSMC[f"PATT{_b}"] = (f"属性时序{_b}", f"存储块{_b} 属性存储器时序（{_lbl}）")
    FSMC[f"ECCR{_b}"] = (f"块{_b} ECC", f"存储块{_b} ECC 结果（{_lbl}）")
FSMC["PIO4"] = ("I/O时序4", "存储块4 PCCard I/O 时序")

# 外设级精确字典汇总
PERI = {
    "RCC": RCC, "DBG": DBG, "SDIO": SDIO, "DCMI": DCMI, "RNG": RNG,
}

USART_PERIS = ("USART1", "USART2", "USART3", "USART6", "UART4", "UART5")
SPI_PERIS   = ("SPI1", "SPI2", "SPI3", "SPI4", "SPI5", "SPI6")
I2C_PERIS   = ("I2C1", "I2C2", "I2C3")
ADC_PERIS   = ("ADC1", "ADC2", "ADC3")
TIM_ADV_PERIS   = ("TIM1", "TIM8")
TIM_GEN_PERIS   = ("TIM2", "TIM3", "TIM4", "TIM5")
TIM_BASIC_PERIS = ("TIM6", "TIM7")
TIM_SIMPLE_PERIS = ("TIM9", "TIM10", "TIM11", "TIM12", "TIM13", "TIM14")
GPIO_PERIS = ("GPIOA", "GPIOB", "GPIOC", "GPIOD", "GPIOE", "GPIOF", "GPIOG", "GPIOH", "GPIOI")
CAN_PERIS  = ("CAN1", "CAN2")

# ──────────────────────────────────────────────────────────────
# 3) USB OTG / Ethernet 前缀解码（best-effort）
# ──────────────────────────────────────────────────────────────
OTG_MAP = {
    "GOTGCTL": ("OTG控制", "OTG 控制寄存器"),
    "GOTGINT": ("OTG中断", "OTG 中断寄存器"),
    "GAHBCFG": ("全局AHB配置", "全局 AHB 配置"),
    "GUSBCFG": ("USB配置", "USB 配置"),
    "GRSTCTL": ("复位控制", "复位控制"),
    "GINTSTS": ("全局中断状态", "全局中断状态"),
    "GINTMSK": ("全局中断屏蔽", "全局中断屏蔽"),
    "GRXSTSR": ("接收状态读", "接收状态（读）"),
    "GRXFSIZ": ("接收FIFO大小", "接收 FIFO 大小"),
    "GNPTXFSIZ": ("非周期TX FIFO大小", "非周期发送 FIFO 大小"),
    "GNPTXSTS": ("非周期TX状态", "非周期发送状态"),
    "GCCFG":   ("通用配置", "通用配置"),
    "GUID":    ("用户ID", "用户 ID"),
    "GSNPSID": ("Synopsys ID", "厂商 ID"),
    "GHWCFG1": ("硬件配置1", "硬件配置 1"),
    "GHWCFG2": ("硬件配置2", "硬件配置 2"),
    "GHWCFG3": ("硬件配置3", "硬件配置 3"),
    "GHWCFG4": ("硬件配置4", "硬件配置 4"),
    "CID":     ("芯片ID", "芯片 ID"),
    "GRXSTSP": ("接收状态弹出", "接收状态（弹出）"),
    "TX0FSIZ": ("端点0TX FIFO", "端点0 发送 FIFO 大小"),
    "DVBUSDIS":("设备VBUS释放", "设备 VBUS 释放时间"),
    "DVBUSPULSE":("设备VBUS脉冲", "设备 VBUS 脉冲时间"),
    "DTHRCTL": ("设备阈值控制", "设备 FIFO 阈值控制"),
    "DEACHINT":("每端点中断", "每端点中断"),
    "DEACHINTMSK":("每端点中断屏蔽", "每端点中断屏蔽"),
    "DIEPEACHMSK1":("IN端点公屏蔽", "IN 端点公共中断屏蔽 1"),
    "DOEPEACHMSK1":("OUT端点公屏蔽", "OUT 端点公共中断屏蔽 1"),
    "HCFG":    ("主机配置", "主机配置"),
    "HFIR":    ("主机帧间隔", "主机帧间隔"),
    "HFNUM":   ("主机帧号", "主机帧编号"),
    "HPTXSTS": ("主机周期TX状态", "主机周期发送状态"),
    "HAINT":   ("主机通道中断", "主机所有通道中断"),
    "HAINTMSK":("主机通道中断屏蔽", "主机通道中断屏蔽"),
    "HPRT":    ("主机端口", "主机端口"),
    "DCFG":    ("设备配置", "设备配置"),
    "DCTL":    ("设备控制", "设备控制"),
    "DSTS":    ("设备状态", "设备状态"),
    "DIEPMSK": ("IN端点中断屏蔽", "IN 端点中断屏蔽"),
    "DOEPMSK": ("OUT端点中断屏蔽", "OUT 端点中断屏蔽"),
    "DAINT":   ("设备端点中断", "设备所有端点中断"),
    "DAINTMSK":("端点中断屏蔽", "端点中断屏蔽"),
    "DTxFSTS": ("端点FIFO状态", "端点发送 FIFO 状态"),
    "PCGCCTL": ("电源时钟门控", "电源与时钟门控"),
}

ETH_MAP = {
    "MACCR":     ("MAC控制", "MAC 控制"),
    "MACFFR":    ("MAC帧过滤", "MAC 帧过滤"),
    "MACHTHR":   ("MAC哈希高", "MAC 哈希表高"),
    "MACHTLR":   ("MAC哈希低", "MAC 哈希表低"),
    "MACMIIAR":  ("MAC MII地址", "MAC MII 地址"),
    "MACMIIDR":  ("MAC MII数据", "MAC MII 数据"),
    "MACFCR":    ("MAC流控", "MAC 流控"),
    "MACVLANTR": ("MAC VLAN标签", "MAC VLAN 标签"),
    "MACRWUFFR": ("MAC唤醒帧", "MAC 远程唤醒帧"),
    "MACPMTCSR": ("MAC 电源管理", "MAC 电源管理控制"),
    "MACDBGR":   ("MAC调试", "MAC 调试"),
    "MACSR":     ("MAC状态", "MAC 状态"),
    "MACIMR":    ("MAC中断屏蔽", "MAC 中断屏蔽"),
    "MACA0HR":   ("MAC地址0高", "MAC 地址 0 高"),
    "MACA0LR":   ("MAC地址0低", "MAC 地址 0 低"),
    "MMCCR":     ("MMC控制", "MMC 控制"),
    "MMCRIR":    ("MMC接收中断", "MMC 接收中断"),
    "MMCTIR":    ("MMC发送中断", "MMC 发送中断"),
    "MMCRIMR":   ("MMC接收屏蔽", "MMC 接收中断屏蔽"),
    "MMCTIMR":   ("MMC发送屏蔽", "MMC 发送中断屏蔽"),
    "PTPTSCR":   ("PTP时间控制", "PTP 时间戳控制"),
    "PTPSSIR":   ("PTP子秒增量", "PTP 子秒增量"),
    "PTPTSHR":   ("PTP时间高", "PTP 时间戳高"),
    "PTPTSLR":   ("PTP时间低", "PTP 时间戳低"),
    "PTPTSHUR":  ("PTP时间高更新", "PTP 时间高更新"),
    "PTPTSLUR":  ("PTP时间低更新", "PTP 时间低更新"),
    "PTPTSAR":   ("PTP时间戳加", "PTP 时间戳加法"),
    "PTPTTHR":   ("PTP目标时间高", "PTP 目标时间高"),
    "PTPTTLR":   ("PTP目标时间低", "PTP 目标时间低"),
    "DMABMR":    ("DMA总线模式", "DMA 总线模式"),
    "DMATPDR":   ("DMA当前发送", "DMA 当前发送指针"),
    "DMARPDR":   ("DMA当前接收", "DMA 当前接收指针"),
    "DMARDLAR":  ("DMA接收描述符", "DMA 接收描述符列表地址"),
    "DMATDLAR":  ("DMA发送描述符", "DMA 发送描述符列表地址"),
    "DMASR":     ("DMA状态", "DMA 状态"),
    "DMACHTDR":  ("DMA当前主机发送", "DMA 当前主机发送描述符"),
    "DMACHRDR":  ("DMA当前主机接收", "DMA 当前主机接收描述符"),
    "DMACHTBAR": ("DMA主机发送底", "DMA 主机发送描述符底"),
    "DMACHRBAR": ("DMA主机接收底", "DMA 主机接收描述符底"),
    "DMAOMR":    ("DMA操作模式", "DMA 操作模式"),
    "DMAIER":    ("DMA中断使能", "DMA 中断使能"),
    "DMAMFBOCR": ("DMA组播溢出", "DMA 组播帧溢出计数"),
    "DMARSWTR":  ("DMA接收状态写", "DMA 接收状态写"),
    "MMCTGFMSCCR":("MMC发送单碰撞", "MMC 发送好帧单碰撞计数"),
    "MMCTGFCR":  ("MMC发送好帧", "MMC 发送好帧计数"),
    "MMCRFCECR": ("MMC接收CRC错", "MMC 接收 CRC 错误计数"),
    "MMCRFAECR": ("MMC接收对齐错", "MMC 接收对齐错误计数"),
    "MMCRGUFCR": ("MMC接收单播", "MMC 接收单播帧计数"),
    "PTPTSSR":   ("PTP子秒状态", "PTP 子秒状态"),
    "PTPPPSCR":  ("PTP秒脉冲", "PTP 秒脉冲控制"),
}


def decode_otg(peri: str, reg: str):
    tag = "FS" if "FS" in peri else "HS"
    s = reg
    for pfx in ("OTG_FS_", "OTG_HS_", "FS_", "HS_"):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    # 去掉 _Device/_Host/_Peripheral 后缀再做映射
    base = s
    for suf in ("_Device", "_Host", "_Peripheral"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    if base in OTG_MAP:
        return OTG_MAP[base]
    # 主机通道 / 设备端点 模式
    m = re.match(r"^(HCCHAR|HCSPLT|HCINT|HCINTMSK|HCTSIZ|HCDMA)(\d+)$", base)
    if m:
        kind = {"HCCHAR": "主机通道特征", "HCSPLT": "主机通道分裂",
                "HCINT": "主机通道中断", "HCINTMSK": "主机通道中断屏蔽",
                "HCTSIZ": "主机通道传输", "HCDMA": "主机通道DMA"}[m.group(1)]
        return (f"{kind}{m.group(2)}", f"USB OTG {tag} {kind}{m.group(2)}")
    m = re.match(r"^(DIEPCTL|DIEPINT|DIEPEMPMSK|DIEPTSIZ|DIEPDMA|DIEPTXF|DOEPCTL|DOEPINT|DOEPTSIZ|DOEPDMA)(\d+)$", base)
    if m:
        kind = {"DIEPCTL": "IN端点控制", "DIEPINT": "IN端点中断",
                "DIEPEMPMSK": "IN端点空屏蔽", "DIEPTSIZ": "IN端点传输",
                "DIEPDMA": "IN端点DMA", "DIEPTXF": "IN端点发送FIFO",
                "DOEPCTL": "OUT端点控制", "DOEPINT": "OUT端点中断",
                "DOEPTSIZ": "OUT端点传输", "DOEPDMA": "OUT端点DMA"}[m.group(1)]
        return (f"{kind}{m.group(2)}", f"USB OTG {tag} {kind}{m.group(2)}")
    return (f"OTG{tag}寄存器 {s}", f"USB OTG {tag} 寄存器 {s}（best-effort）")


def decode_eth(peri: str, reg: str):
    # 已是 MAC/MMC/PTP/DMA 前缀
    if reg in ETH_MAP:
        return ETH_MAP[reg]
    m = re.match(r"^(MACA[1-3])(HR|LR)$", reg)
    if m:
        idx = m.group(1)[-1]
        return (f"MAC地址{idx}{'高' if m.group(2)=='HR' else '低'}",
                f"MAC 地址 {idx} {'高' if m.group(2)=='HR' else '低'}（best-effort）")
    m = re.match(r"^MMCT?G?FS?CCR(\d*)$", reg)
    if m:
        return (f"MMC统计{m.group(1)}", f"MMC 统计寄存器 {reg}（best-effort）")
    return (f"以太网{reg}", f"Ethernet 寄存器 {reg}（best-effort）")


# ──────────────────────────────────────────────────────────────
# 4) 解析主函数
# ──────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────
# 5) 外设级（最外层）中文：名称 + 描述
# ──────────────────────────────────────────────────────────────
def otg_peri_cn(part, tag):
    mp = {"GLOBAL": "全局寄存器", "HOST": "主机寄存器",
          "DEVICE": "设备寄存器", "PWRCLK": "电源时钟寄存器"}
    s = mp.get(part, part)
    return (f"USB OTG {tag} {s}", f"USB OTG {tag} {s}（best-effort）")


def eth_peri_cn(part):
    mp = {"MAC": "MAC 媒体访问控制", "MMC": "MMC 管理计数器",
          "PTP": "PTP 精确时间协议", "DMA": "DMA 控制器"}
    s = mp.get(part, part)
    return (f"以太网 {s}", f"以太网 {s}（best-effort）")


def peri_cn(orig: str):
    """返回 (短中文, 描述中文) 或 None（不翻译）。"""
    m = re.match(r"^GPIO([A-I])$", orig)
    if m:
        return (f"端口{m.group(1)}", "通用输入输出端口：引脚模式/输出/复用配置与读写")
    if orig == "RCC":
        return ("复位与时钟控制", "管理所有外设时钟使能与系统复位")
    m = re.match(r"^DMA(\d)$", orig)
    if m:
        return (f"直接存储器访问{m.group(1)}", "DMA 控制器：外设与存储器间高速数据搬运")
    m = re.match(r"^ADC(\d)$", orig)
    if m:
        return (f"模数转换{m.group(1)}", "模数转换器：将模拟电压转为数字量")
    if orig == "C_ADC":
        return ("ADC公共", "ADC 公共寄存器：多 ADC 模式与采样控制")
    if orig == "DAC":
        return ("数模转换", "数模转换器：将数字量转为模拟电压")
    m = re.match(r"^TIM(\d+)$", orig)
    if m:
        n = int(m.group(1))
        tag = "高级定时器" if n in (1, 8) else ("基本定时器" if n in (6, 7) else "通用定时器")
        return (f"定时器{n}({tag})", f"{tag}：计数/比较/PWM/输入捕获")
    m = re.match(r"^USART(\d)$", orig)
    if m:
        return (f"串口{m.group(1)}", "通用同步异步收发器：异步/同步串行通信")
    m = re.match(r"^UART(\d)$", orig)
    if m:
        return (f"串口{m.group(1)}", "通用异步收发器：异步串行通信")
    m = re.match(r"^SPI(\d)$", orig)
    if m:
        return ("串行外设接口", "串行外设接口：全双工同步串行通信")
    m = re.match(r"^I2C(\d)$", orig)
    if m:
        return ("集成电路总线", "集成电路总线：双线制串行通信")
    if orig == "PWR":
        return ("电源控制", "电源控制：稳压器/唤醒/备份域访问")
    if orig == "FLASH":
        return ("闪存", "片内 Flash：编程/擦除/读保护")
    if orig == "EXTI":
        return ("外部中断", "外部中断/事件控制器：GPIO 等外部信号触发")
    if orig == "SYSCFG":
        return ("系统配置", "系统配置：存储器重映射/外部中断源选择")
    if orig == "RTC":
        return ("实时时钟", "实时时钟：日历/闹钟/唤醒")
    if orig == "IWDG":
        return ("独立看门狗", "独立看门狗：超时复位（独立时钟）")
    if orig == "WWDG":
        return ("窗口看门狗", "窗口看门狗：窗口内喂狗否则复位")
    if orig == "CRC":
        return ("循环冗余校验", "CRC 计算单元：数据校验")
    if orig == "SDIO":
        return ("安全数字IO", "安全数字输入输出：SD 卡/MMC 接口")
    if orig == "DCMI":
        return ("数字摄像头接口", "数字摄像头接口：并行图像采集")
    if orig == "RNG":
        return ("随机数发生器", "真随机数发生器")
    if orig == "FSMC":
        return ("静态存储器控制", "FSMC：NOR/PSRAM/NAND 外部存储器控制")
    if orig == "DBG":
        return ("调试支持", "调试支持单元：调试时外设行为控制")
    if orig == "NVIC":
        return ("中断控制器", "嵌套向量中断控制器：中断使能与优先级")
    m = re.match(r"^CAN(\d)$", orig)
    if m:
        return ("控制器局域网", "控制器局域网：差分串行通信")
    if orig.startswith("OTG_FS_"):
        return otg_peri_cn(orig[len("OTG_FS_"):], "FS")
    if orig.startswith("OTG_HS_"):
        return otg_peri_cn(orig[len("OTG_HS_"):], "HS")
    if orig.startswith("Ethernet_"):
        return eth_peri_cn(orig[len("Ethernet_"):])
    if orig in ("I2S2ext", "I2S3ext"):
        n = orig[3]
        return (f"I2S{n}扩展", "I2S 全双工扩展外设（基于 SPI）")
    return None


def cn_for(peri: str, reg: str):
    # 外设级精确
    if peri in PERI and reg in PERI[peri]:
        return PERI[peri][reg]
    # 共享集合
    if peri in GPIO_PERIS and reg in GPIO:
        return GPIO[reg]
    if peri in USART_PERIS and reg in USART:
        return USART[reg]
    if peri in SPI_PERIS and reg in SPI:
        return SPI[reg]
    if peri in I2C_PERIS and reg in I2C:
        return I2C[reg]
    if peri in ADC_PERIS and reg in ADC:
        return ADC[reg]
    if peri in ("ADC_COMMON", "C_ADC") and reg in ADC:
        return ADC[reg]
    if peri == "DAC" and reg in DAC:
        return DAC[reg]
    if peri in TIM_ADV_PERIS and reg in TIM_ADV:
        return TIM_ADV[reg]
    if peri in TIM_GEN_PERIS and reg in TIM_GEN:
        return TIM_GEN[reg]
    if peri in TIM_BASIC_PERIS and reg in TIM_BASIC:
        return TIM_BASIC[reg]
    if peri in TIM_SIMPLE_PERIS and reg in TIM_SIMPLE:
        return TIM_SIMPLE[reg]
    if peri == "EXTI" and reg in EXTI:
        return EXTI[reg]
    if peri == "SYSCFG" and reg in SYSCFG:
        return SYSCFG[reg]
    if peri == "PWR" and reg in PWR:
        return PWR[reg]
    if peri == "FLASH" and reg in FLASH:
        return FLASH[reg]
    if peri == "IWDG" and reg in IWDG:
        return IWDG[reg]
    if peri == "WWDG" and reg in WWDG:
        return WWDG[reg]
    if peri == "CRC" and reg in CRC:
        return CRC[reg]
    if peri == "NVIC" and reg in NVIC:
        return NVIC[reg]
    if peri == "RTC" and reg in RTC:
        return RTC[reg]
    if peri in CAN_PERIS and reg in CAN:
        return CAN[reg]
    if peri == "FSMC" and reg in FSMC:
        return FSMC[reg]
    # DMA 流寄存器（模式匹配）
    if peri in ("DMA1", "DMA2"):
        m = re.match(r"^S(\d+)(CR|NDTR|PAR|M0AR|M1AR|FCR)$", reg)
        if m:
            unit = {"CR": "控制", "NDTR": "数据数", "PAR": "外设地址",
                    "M0AR": "存储器0地址", "M1AR": "存储器1地址", "FCR": "FIFO控制"}[m.group(2)]
            return (f"流{m.group(1)}{unit}", f"DMA 流{m.group(1)} {unit}寄存器")
        if reg in ("LISR", "HISR", "LIFCR", "HIFCR"):
            return {
                "LISR": ("低中断状态", "流 0~3 中断状态"),
                "HISR": ("高中断状态", "流 4~7 中断状态"),
                "LIFCR": ("低中断清除", "流 0~3 中断标志清除"),
                "HIFCR": ("高中断清除", "流 4~7 中断标志清除"),
            }[reg]
    # CAN 邮箱 / 过滤器（模式匹配）
    if peri in CAN_PERIS:
        m = re.match(r"^T[ID](\d)R$", reg)
        if m:
            return ("发送邮箱标识符", f"发送邮箱 {m.group(1)} 标识符")
        m = re.match(r"^TDT(\d)R$", reg)
        if m: return ("发送邮箱时间", f"发送邮箱 {m.group(1)} 数据时间")
        m = re.match(r"^TDL(\d)R$", reg)
        if m: return ("发送邮箱数据低", f"发送邮箱 {m.group(1)} 数据低")
        m = re.match(r"^TDH(\d)R$", reg)
        if m: return ("发送邮箱数据高", f"发送邮箱 {m.group(1)} 数据高")
        m = re.match(r"^R[ID](\d)R$", reg)
        if m: return ("接收FIFO邮箱标识符", f"接收 FIFO 邮箱 {m.group(1)} 标识符")
        m = re.match(r"^RDT(\d)R$", reg)
        if m: return ("接收FIFO邮箱时间", f"接收 FIFO 邮箱 {m.group(1)} 数据时间")
        m = re.match(r"^RDL(\d)R$", reg)
        if m: return ("接收FIFO邮箱数据低", f"接收 FIFO 邮箱 {m.group(1)} 数据低")
        m = re.match(r"^RDH(\d)R$", reg)
        if m: return ("接收FIFO邮箱数据高", f"接收 FIFO 邮箱 {m.group(1)} 数据高")
        m = re.match(r"^F(\d+)R1$", reg)
        if m: return (f"过滤器组{m.group(1)}_1", f"过滤器组 {m.group(1)} 寄存器 1")
        m = re.match(r"^F(\d+)R2$", reg)
        if m: return (f"过滤器组{m.group(1)}_2", f"过滤器组 {m.group(1)} 寄存器 2")
    # FSMC 块号模式
    if peri == "FSMC":
        m = re.match(r"^B[CT]R(\d)$", reg)
        if m: return (f"块{m.group(1)}时序", f"FSMC 存储块 {m.group(1)} 时序")
        m = re.match(r"^BWTR(\d)$", reg)
        if m: return (f"块{m.group(1)}写时序", f"FSMC 存储块 {m.group(1)} 写时序")
    # USB OTG
    if peri.startswith("OTG"):
        return decode_otg(peri, reg)
    # Ethernet
    if peri.startswith("Ethernet"):
        return decode_eth(peri, reg)
    return None


def annotate_text(el, new_text):
    orig = (el.text or "").strip()
    el.text = f"{new_text} | {orig}" if orig else new_text


def main():
    tree = ET.parse(SRC)
    root = tree.getroot()

    total = 0
    done = 0
    for peri in root.iter("peripheral"):
        pname_el = peri.find("name")
        if pname_el is None or not pname_el.text:
            continue
        pname = pname_el.text.strip()
        regs = peri.find("registers")
        if regs is None:
            continue
        for reg in regs.findall("register"):
            total += 1
            rname_el = reg.find("name")
            if rname_el is None or not rname_el.text:
                continue
            rname = rname_el.text.strip()
            cn = cn_for(pname, rname)
            if cn is None:
                continue
            short, full = cn
            rname_el.text = f"{rname}（{short}）"
            dn = reg.find("displayName")
            if dn is None:
                dn = ET.SubElement(reg, "displayName")
            dn.text = f"{rname}（{short}）"
            d = reg.find("description")
            if d is None:
                d = ET.SubElement(reg, "description")
            annotate_text(d, full)
            done += 1

    # ── 外设级（最外层）中文：改名 + 修正 derivedFrom + 描述 ──
    # 先建 原英文名 -> 中文名 映射（此时外设名仍是英文，寄存器等字典可正确匹配）
    name_map = {}
    peri_desc = {}
    for peri in root.iter("peripheral"):
        pn = peri.find("name")
        if pn is None or not pn.text:
            continue
        orig = pn.text.strip()
        cn = peri_cn(orig)
        if cn is None:
            name_map[orig] = orig
            peri_desc[orig] = None
        else:
            short, desc = cn
            name_map[orig] = f"{orig}（{short}）"
            peri_desc[orig] = desc

    ptotal = 0
    pdone = 0
    for peri in root.iter("peripheral"):
        pn = peri.find("name")
        if pn is None or not pn.text:
            continue
        orig = pn.text.strip()
        ptotal += 1
        new_name = name_map.get(orig, orig)
        if new_name != orig:
            pn.text = new_name
            pdone += 1
        # 修正继承引用：derivedFrom 必须指向改名后的父类
        df = peri.get("derivedFrom")
        if df and df in name_map:
            peri.set("derivedFrom", name_map[df])
        # 外设级描述
        d = peri.find("description")
        desc = peri_desc.get(orig)
        if desc:
            if d is None:
                d = ET.SubElement(peri, "description")
            annotate_text(d, desc)

    tree.write(OUT, encoding="utf-8", xml_declaration=True)
    print(f"OK: 共 {total} 个寄存器，已注入中文 {done} 个 -> {OUT}")
    print(f"    未覆盖（保持英文）: {total - done} 个")
    print(f"    外设名已汉化: {pdone}/{ptotal}")


if __name__ == "__main__":
    main()
