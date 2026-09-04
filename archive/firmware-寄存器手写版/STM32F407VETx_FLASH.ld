/* STM32F407VET6 链接脚本
 * Flash: 512 KB @ 0x08000000
 * SRAM1: 128 KB @ 0x20000000  (主栈在此顶端)
 * CCM:    64 KB @ 0x10000000
 */
ENTRY(Reset_Handler)

_estack = 0x20020000;          /* 主栈顶 = SRAM1 顶端 */

_Min_Heap_Size = 0x200;
_Min_Stack_Size = 0x400;

MEMORY
{
  FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 512K
  RAM     (xrw) : ORIGIN = 0x20000000, LENGTH = 128K
  CCMRAM  (xrw) : ORIGIN = 0x10000000, LENGTH = 64K
}

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } >FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text)
    *(.text*)
    *(.glue_7)
    *(.glue_7t)
    *(.eh_frame)
    . = ALIGN(4);
    _etext = .;
  } >FLASH

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata*)
    . = ALIGN(4);
  } >FLASH

  _sidata = LOADADDR(.data);

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data)
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } >RAM AT> FLASH

  .bss :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss)
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } >RAM

  ._user_heap_stack :
  {
    . = ALIGN(8);
    PROVIDE(end = .);
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
  } >RAM

  /DISCARD/ :
  {
    *(.ARM.exidx*)
    *(.gnu.linkonce.ar*)
  }
}
