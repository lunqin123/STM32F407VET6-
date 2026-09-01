TARGET  := firmware
BUILD   := build
SRC     := src/main.c src/startup_stm32f407xx.s

# 强制用 POSIX shell（Git Bash 的 sh）执行配方。
# 否则在 Windows CMD 下 "mkdir -p" 会被当成"建名为 -p 的目录"而报错。
SHELL   := sh

# Cortex-M4 + FPU（硬浮点）
CPU     := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard

# 裸机：-nostdlib 不链接 libc/crt0（启动代码由 startup 提供）
CFLAGS  := $(CPU) -O0 -g3 -Wall -fdata-sections -ffunction-sections -nostdlib
LDFLAGS := $(CPU) -TSTM32F407VETx_FLASH.ld -Wl,--gc-sections \
           -Wl,-Map=$(BUILD)/$(TARGET).map -nostdlib

CC      := arm-none-eabi-gcc
OBJCOPY := arm-none-eabi-objcopy
SIZE    := arm-none-eabi-size
OPENOCD := openocd

# 下载器接口：克隆版 ST-Link V2 用 stlink-v2.cfg；若报找不到接口改 stlink.cfg
STLINK_IF := interface/stlink-v2.cfg

all: $(BUILD)/$(TARGET).elf $(BUILD)/$(TARGET).bin $(BUILD)/$(TARGET).hex

$(BUILD)/$(TARGET).elf: $(SRC)
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $(SRC) -o $@ $(LDFLAGS)
	$(OBJCOPY) -O binary $@ $(BUILD)/$(TARGET).bin
	$(OBJCOPY) -O ihex   $@ $(BUILD)/$(TARGET).hex
	$(SIZE) $@

# 烧录进 Flash（编译后通过 OpenOCD 写片 + 校验 + 复位）
flash: all
	$(OPENOCD) -f $(STLINK_IF) -f target/stm32f4x.cfg \
	  -c "program $(BUILD)/$(TARGET).elf verify reset exit"

clean:
	rm -rf $(BUILD)

.PHONY: all clean flash
