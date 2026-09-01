.syntax unified
.cpu cortex-m4
.fpu softvfp
.thumb

.global g_pfnVectors
.global Reset_Handler
.global Default_Handler

/* 中断向量表：首字为初始主栈指针，次字为复位入口 */
.section .isr_vector,"a",%progbits
.type g_pfnVectors, %object
.size g_pfnVectors, . - g_pfnVectors
g_pfnVectors:
  .word _estack
  .word Reset_Handler
  .word NMI_Handler
  .word HardFault_Handler
  .word MemManage_Handler
  .word BusFault_Handler
  .word UsageFault_Handler
  .word 0
  .word 0
  .word 0
  .word 0
  .word SVC_Handler
  .word DebugMon_Handler
  .word 0
  .word PendSV_Handler
  .word SysTick_Handler
  /* 外部中断 */
  .word WWDG_IRQHandler
  .word PVD_IRQHandler
  .word TAMP_STAMP_IRQHandler
  .word RTC_WKUP_IRQHandler
  .word FLASH_IRQHandler
  .word RCC_IRQHandler
  .word EXTI0_IRQHandler
  .word EXTI1_IRQHandler
  .word EXTI2_IRQHandler
  .word EXTI3_IRQHandler
  .word EXTI4_IRQHandler
  .word DMA1_Stream0_IRQHandler
  .word DMA1_Stream1_IRQHandler
  .word DMA1_Stream2_IRQHandler
  .word DMA1_Stream3_IRQHandler
  .word DMA1_Stream4_IRQHandler
  .word DMA1_Stream5_IRQHandler
  .word DMA1_Stream6_IRQHandler
  .word ADC_IRQHandler
  .word CAN1_TX_IRQHandler
  .word CAN1_RX0_IRQHandler
  .word CAN1_RX1_IRQHandler
  .word CAN1_SCE_IRQHandler
  .word EXTI9_5_IRQHandler
  .word TIM1_BRK_TIM9_IRQHandler
  .word TIM1_UP_TIM10_IRQHandler
  .word TIM1_TRG_COM_TIM11_IRQHandler
  .word TIM1_CC_IRQHandler
  .word TIM2_IRQHandler
  .word TIM3_IRQHandler
  .word TIM4_IRQHandler
  .word TIM5_IRQHandler
  .word TIM6_DAC_IRQHandler
  .word TIM7_IRQHandler
  .word DMA2_Stream0_IRQHandler
  .word DMA2_Stream1_IRQHandler
  .word DMA2_Stream2_IRQHandler
  .word DMA2_Stream3_IRQHandler
  .word DMA2_Stream4_IRQHandler
  .word ETH_IRQHandler
  .word ETH_WKUP_IRQHandler
  .word CAN2_TX_IRQHandler
  .word CAN2_RX0_IRQHandler
  .word CAN2_RX1_IRQHandler
  .word CAN2_SCE_IRQHandler
  .word OTG_FS_IRQHandler
  .word DMA2_Stream5_IRQHandler
  .word DMA2_Stream6_IRQHandler
  .word DMA2_Stream7_IRQHandler
  .word USART6_IRQHandler
  .word I2C3_EV_IRQHandler
  .word I2C3_ER_IRQHandler
  .word OTG_HS_EP1_OUT_IRQHandler
  .word OTG_HS_EP1_IN_IRQHandler
  .word OTG_HS_WKUP_IRQHandler
  .word OTG_HS_IRQHandler
  .word DCMI_IRQHandler
  .word 0
  .word HASH_RNG_IRQHandler
  .word FPU_IRQHandler
  .word UART7_IRQHandler
  .word UART8_IRQHandler
  .word SPI4_IRQHandler
  .word SPI5_IRQHandler
  .word SPI6_IRQHandler
  .word SAI1_IRQHandler
  .word SAI2_IRQHandler
  .word QUADSPI_IRQHandler
  .word LTDC_IRQHandler
  .word LTDC_ER_IRQHandler
  .word DMA2D_IRQHandler

.section .text.Reset_Handler
.type Reset_Handler, %function
Reset_Handler:
  ldr sp, =_estack
  /* 将 .data 初值从 FLASH 复制到 RAM */
  ldr r0, =_sdata
  ldr r1, =_edata
  ldr r2, =_sidata
  movs r3, #0
  b LoopCopyDataInit
CopyDataInit:
  ldr r4, [r2, r3]
  str r4, [r0, r3]
  adds r3, r3, #4
LoopCopyDataInit:
  adds r4, r0, r3
  cmp r4, r1
  bcc CopyDataInit
  /* 清零 .bss */
  ldr r2, =_sbss
  ldr r4, =_ebss
  movs r3, #0
  b LoopFillBss
FillBss:
  str r3, [r2]
  adds r2, r2, #4
LoopFillBss:
  cmp r2, r4
  bcc FillBss
  /* 进入 main */
  bl main
  b .
.size Reset_Handler, . - Reset_Handler

.section .text.Default_Handler,"ax",%progbits
Default_Handler:
Infinite_Loop:
  b Infinite_Loop
.size Default_Handler, . - Default_Handler

/* 所有中断/异常 handler 弱别名到 Default_Handler（死循环） */
.macro IRQ_ALIAS name
  .weak \name
  .set \name, Default_Handler
.endm

IRQ_ALIAS NMI_Handler
IRQ_ALIAS HardFault_Handler
IRQ_ALIAS MemManage_Handler
IRQ_ALIAS BusFault_Handler
IRQ_ALIAS UsageFault_Handler
IRQ_ALIAS SVC_Handler
IRQ_ALIAS DebugMon_Handler
IRQ_ALIAS PendSV_Handler
IRQ_ALIAS SysTick_Handler
IRQ_ALIAS WWDG_IRQHandler
IRQ_ALIAS PVD_IRQHandler
IRQ_ALIAS TAMP_STAMP_IRQHandler
IRQ_ALIAS RTC_WKUP_IRQHandler
IRQ_ALIAS FLASH_IRQHandler
IRQ_ALIAS RCC_IRQHandler
IRQ_ALIAS EXTI0_IRQHandler
IRQ_ALIAS EXTI1_IRQHandler
IRQ_ALIAS EXTI2_IRQHandler
IRQ_ALIAS EXTI3_IRQHandler
IRQ_ALIAS EXTI4_IRQHandler
IRQ_ALIAS DMA1_Stream0_IRQHandler
IRQ_ALIAS DMA1_Stream1_IRQHandler
IRQ_ALIAS DMA1_Stream2_IRQHandler
IRQ_ALIAS DMA1_Stream3_IRQHandler
IRQ_ALIAS DMA1_Stream4_IRQHandler
IRQ_ALIAS DMA1_Stream5_IRQHandler
IRQ_ALIAS DMA1_Stream6_IRQHandler
IRQ_ALIAS ADC_IRQHandler
IRQ_ALIAS CAN1_TX_IRQHandler
IRQ_ALIAS CAN1_RX0_IRQHandler
IRQ_ALIAS CAN1_RX1_IRQHandler
IRQ_ALIAS CAN1_SCE_IRQHandler
IRQ_ALIAS EXTI9_5_IRQHandler
IRQ_ALIAS TIM1_BRK_TIM9_IRQHandler
IRQ_ALIAS TIM1_UP_TIM10_IRQHandler
IRQ_ALIAS TIM1_TRG_COM_TIM11_IRQHandler
IRQ_ALIAS TIM1_CC_IRQHandler
IRQ_ALIAS TIM2_IRQHandler
IRQ_ALIAS TIM3_IRQHandler
IRQ_ALIAS TIM4_IRQHandler
IRQ_ALIAS TIM5_IRQHandler
IRQ_ALIAS TIM6_DAC_IRQHandler
IRQ_ALIAS TIM7_IRQHandler
IRQ_ALIAS DMA2_Stream0_IRQHandler
IRQ_ALIAS DMA2_Stream1_IRQHandler
IRQ_ALIAS DMA2_Stream2_IRQHandler
IRQ_ALIAS DMA2_Stream3_IRQHandler
IRQ_ALIAS DMA2_Stream4_IRQHandler
IRQ_ALIAS ETH_IRQHandler
IRQ_ALIAS ETH_WKUP_IRQHandler
IRQ_ALIAS CAN2_TX_IRQHandler
IRQ_ALIAS CAN2_RX0_IRQHandler
IRQ_ALIAS CAN2_RX1_IRQHandler
IRQ_ALIAS CAN2_SCE_IRQHandler
IRQ_ALIAS OTG_FS_IRQHandler
IRQ_ALIAS DMA2_Stream5_IRQHandler
IRQ_ALIAS DMA2_Stream6_IRQHandler
IRQ_ALIAS DMA2_Stream7_IRQHandler
IRQ_ALIAS USART6_IRQHandler
IRQ_ALIAS I2C3_EV_IRQHandler
IRQ_ALIAS I2C3_ER_IRQHandler
IRQ_ALIAS OTG_HS_EP1_OUT_IRQHandler
IRQ_ALIAS OTG_HS_EP1_IN_IRQHandler
IRQ_ALIAS OTG_HS_WKUP_IRQHandler
IRQ_ALIAS OTG_HS_IRQHandler
IRQ_ALIAS DCMI_IRQHandler
IRQ_ALIAS HASH_RNG_IRQHandler
IRQ_ALIAS FPU_IRQHandler
IRQ_ALIAS UART7_IRQHandler
IRQ_ALIAS UART8_IRQHandler
IRQ_ALIAS SPI4_IRQHandler
IRQ_ALIAS SPI5_IRQHandler
IRQ_ALIAS SPI6_IRQHandler
IRQ_ALIAS SAI1_IRQHandler
IRQ_ALIAS SAI2_IRQHandler
IRQ_ALIAS QUADSPI_IRQHandler
IRQ_ALIAS LTDC_IRQHandler
IRQ_ALIAS LTDC_ER_IRQHandler
IRQ_ALIAS DMA2D_IRQHandler

.end
