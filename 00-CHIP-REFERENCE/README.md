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

## 备注

原仓库中另外两类文件已在清空时移除，原因如下：

| 原目录 | 处理 | 理由 |
| --- | --- | --- |
| `00-STM32F4xx_HAL_DRIVERS/`、`00-STM32F4xx_STANDARD_PERIPHERAL_DRIVERS/`、`00-STM32F429_LIBRARIES/` | 移入 `D:\_TRASH\STM32F429-legacy-20260901\` | 均为 ST 官方/第三方库源码，随时可从 GitHub 或 ST 官网重新获取，且路线不再使用标准外设库 |
| `01-` 至 `29-` 全部示例目录（120 个裸机外设例程） | 同上 | 属于"通用 MCU 裸机开发"层级，天花板已被锁死（3–5 年月薪 15–22K） |
| `.git`（`MaJerle/stm32f429` 的克隆） | 同上 | 纯净克隆，0 本地改动、0 领先提交，可随时 `git clone` 恢复 |

如需彻底释放磁盘空间，删除 `D:\_TRASH\STM32F429-legacy-20260901\` 即可（478 MB）。
