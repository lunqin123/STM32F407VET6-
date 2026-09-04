/* STM32F407VET6 最小闪烁固件（验证调试链路用）
 *
 * 注意：板载 LED 引脚因开发板而异。
 * 常见的 "STM32F407VET6 核心板" 用户 LED 在 PC13（低电平点亮）。
 * 若你的板子 LED 在别的脚（如 PD12/PD13/PD14/PD15），改下面的 PIN 即可。
 */
#include <stdint.h>

/* --- 寄存器基址（STM32F4 系列） --- */
#define RCC_AHB1ENR   (*(volatile uint32_t *)0x40023830u)
#define GPIOC_MODER   (*(volatile uint32_t *)0x40020800u)
#define GPIOC_BSRR    (*(volatile uint32_t *)0x40020818u)

#define PIN           13u   /* PC13 */

static void delay(volatile uint32_t n)
{
    for (; n; --n) {
        /* 空转；volatile 防止被优化掉 */
    }
}

int main(void)
{
    /* 1) 开启 GPIOC 时钟（AHB1ENR 位2） */
    RCC_AHB1ENR |= (1u << 2);

    /* 2) 配置 PC13 为输出：MODER13 = 0b01（位26置1，位27清0） */
    GPIOC_MODER |= (1u << (PIN * 2));
    GPIOC_MODER &= ~(1u << (PIN * 2 + 1));

    /* 3) 翻转 LED */
    while (1) {
        GPIOC_BSRR = (1u << PIN);            /* 置位 PC13 */
        delay(800000u);
        GPIOC_BSRR = (1u << (PIN + 16));     /* 复位 PC13 */
        delay(800000u);
    }
}
