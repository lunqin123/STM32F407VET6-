# 芯片参考资料（清空时保留）

从原 `MaJerle/stm32f429` 仓库中保留下来的唯一有价值资产。这两份资料都是从 ST 官方重新下载需要额外步骤、且日常开发高频使用的内容。

---

## 01-CMSIS-SVD/ — 芯片寄存器全表（44 MB / 43 个 SVD）

### 这是什么

CMSIS-SVD（System View Description）是 ARM 定义的 XML 标准，用机器可读的方式描述一颗芯片**全部外设寄存器的地址、位域、复位值、访问权限与中断号**。

通俗地说：**这是芯片的"字典"和"寄存器全表"——真正的"芯片指南"。**

### 你的芯片在哪

```
01-CMSIS-SVD/STM32F40x.svd   ← 这就是 STM32F407VET6 的寄存器全表
```

- 版本 1.5，覆盖 F405 / F407 / F415 / F417（同为 F40x，寄存器映射一致）
- 包含 **47 个外设**：TIM1–TIM14、ADC1-3、CAN1-2、ETH（MAC/DMA）、DCMI（摄像头接口）、USB_OTG_FS/HS、SPI/I2C/USART、DMA、CRC、RNG、FMC、SDIO、RTC、WWDG/IWDG 等
- 文件大小 1.9 MB

### 怎么用它

| 场景 | 工具 | 用途 |
| --- | --- | --- |
| 日常开发 | VSCode + **Cortex-Debug** 插件 | 调试时实时查看/修改外设寄存器，位域按名称展开，不用再翻 PDF 手册 |
| Keil MDK | 内置支持 | Pack Installer 之外手动指定 SVD 路径 |
| STM32CubeIDE | 内置支持 | 调试视图的 SVD 路径设置 |
| 命令行查询 | `python -c "..."` 解析 XML | 批量提取某外设所有寄存器，生成自己的驱动模板 |

**对阶段 0（FOC 伺服驱动）的价值极高**：配置 TIM1 互补 PWM + 死区 + 刹车时，需要在 `BDTR`、`CCMR1/2`、`CCER` 之间来回核对位域含义。用 SVD 配合 Cortex-Debug 的寄存器视图，比翻 1700 页的参考手册快一个数量级。

### VSCode 配置示例

```json
// .vscode/launch.json
{
  "configurations": [
    {
      "name": "Cortex Debug",
      "cwd": "${workspaceFolder}",
      "executable": "./build/firmware.elf",
      "request": "launch",
      "type": "cortex-debug",
      "servertype": "openocd",
      "device": "STM32F407VE",
      "svdFile": "${workspaceFolder}/00-CHIP-REFERENCE/01-CMSIS-SVD/STM32F40x.svd"
    }
  ]
}
```

### 其余 42 个 SVD（F0/F1/F2/F3/F4/L0/L1/L4/W 系列）

覆盖 ST 大部分主流 MCU。留着不占什么空间，将来换平台时直接用。

---

## 02-HAL-Manuals/ — HAL 驱动库用户手册（48 MB / 6 个 CHM）

`*.chm` 是 Windows 帮助文档格式，内容是 **STM32F4xx HAL 驱动库的 API 用户手册**（不是芯片数据手册）。

- `STM32F410Rx_User_Manual.chm`
- `STM32F411xE_User_Manual.chm`
- `STM32F417xx_User_Manual.chm`（与 F407 最接近，可作参考）
- `STM32F439xx_User_Manual.chm`
- `STM32F446xx_User_Manual.chm`
- `STM32F479xx_User_Manual.chm`

### 重要提示

**这些是软件库文档，不是芯片手册。** 在 Windows 10/11 上打开 CHM 可能遇到"无法显示"的问题，原因是微软的安全补丁阻止了网络下载的 CHM。解决方法：右键 `.chm` → 属性 → 底部勾选"解除锁定" → 确定。

如果后续路线以 Zephyr / libopencm3 / 直接寄存器操作为主，这份文档的用处会下降，但保留成本为零，暂不动。

---

## 03-PINOUT/ — 引脚分配表（机器可读，非 PDF）

SVD 只描述**寄存器地址**，故意不含任何封装/引脚信息；引脚定义在数据手册 (DocID022152) 的 Pinout / Pin-description 表。本目录把那张表结构化、机器可读化，专门回答"某个外设信号在哪些引脚上 / 某个引脚能接什么外设"。

来源 (GitHub, 官方权威)：
- **stm32duino / Arduino_Core_STM32** → `variants/STM32F4xx/F407V(E-G)T_F417V(E-G)T/PeripheralPins.c`
- 该文件由 ST 的 CubeMX 数据库 (DB 6.0.180) 自动生成，覆盖 F407V(E-G)Tx / F417V(E-G)Tx（F407VET6 属于此族，LQFP100）

文件清单：

| 文件 | 作用 |
| --- | --- |
| `STM32F407VET6_PeripheralPins.c` | **权威机器可读源**：`WEAK const PinMap` 数组，逐引脚列出可接的外设 + GPIO 复用功能编号 (AF)。可直接 `grep` 查询 |
| `STM32F407VET6_pinout.md` | 人读版速查表：ADC / USART / SPI / I2C / CAN / TIM-PWM 的引脚与 AF 汇总 |
| `pins.csv` | 由 `.c` 解析出的 `pin,peripheral,signal,af` 表格，可被脚本/表格直接加载（239 行） |
| `parse_pins.py` | 解析 `.c` → `pins.csv` 的脚本；`python parse_pins.py PA_11` 可即查某引脚能接的外设 |

查询示例：

```bash
python parse_pins.py PA_11
# TIM1  TIM1_CH4     AF1
# USART1             AF7
# CAN1               AF9
# USB_OTG_FS USB_OTG_FS_DM  AF10
```

> 注意：此文件是 2026-09-02 通过 WebFetch 读取 raw GitHub 链接逐字转录所得（沙箱 Bash 出站网络被禁，无法 curl 原始字节）。如需字节级原始文件，浏览器/ git 直接打开上面 Raw URL 即可，内容一致。

---

## 备注

原仓库中另外两类文件已在清空时移除，原因如下：

| 原目录 | 处理 | 理由 |
| --- | --- | --- |
| `00-STM32F4xx_HAL_DRIVERS/`、`00-STM32F4xx_STANDARD_PERIPHERAL_DRIVERS/`、`00-STM32F429_LIBRARIES/` | 移入 `D:\_TRASH\STM32F429-legacy-20260901\` | 均为 ST 官方/第三方库源码，随时可从 GitHub 或 ST 官网重新获取，且路线不再使用标准外设库 |
| `01-` 至 `29-` 全部示例目录（120 个裸机外设例程） | 同上 | 属于"通用 MCU 裸机开发"层级，天花板已被锁死（3–5 年月薪 15–22K） |
| `.git`（`MaJerle/stm32f429` 的克隆） | 同上 | 纯净克隆，0 本地改动、0 领先提交，可随时 `git clone` 恢复 |

如需彻底释放磁盘空间，删除 `D:\_TRASH\STM32F429-legacy-20260901\` 即可（478 MB）。
