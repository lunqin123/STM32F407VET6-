#include "Screen.h"

namespace oled {

static U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

constexpr uint8_t MAX_BANDS = 8;
struct Band {
    uint8_t y_tile;    // 起始 tile 行（每 tile 8px）
    uint8_t h_tile;    // tile 行数
    bool    live;      // true=动态带（局部重发）false=静态带（全帧重画）
    DrawFn  draw;
    bool    dirty;
};
static Band  bands[MAX_BANDS];
static uint8_t n_bands   = 0;
static bool  online      = false;
static bool  awake       = true;
static uint32_t sleep_timeout = 30000UL;   // 默认 30s 无活动自动息屏
static uint32_t last_activity = 0;
static uint32_t last_full     = 0;         // 全帧发送节流
static uint32_t last_live     = 0;         // 动态带局部发送节流
static uint32_t last_black    = 0;         // 息屏兜底重推节流

static void pushBlackFrame()
{
    u8g2.clearBuffer();
    u8g2.sendBuffer();
}

bool begin()
{
    online = u8g2.begin();
    if (online) {
        last_activity = millis();
        last_full     = millis();
    }
    return online;
}

void addBand(uint8_t y_tile, uint8_t h_tile, bool live, DrawFn draw)
{
    if (!online || n_bands >= MAX_BANDS || draw == nullptr) return;
    bands[n_bands] = { y_tile, h_tile, live, draw, true };   // 新带默认脏 → 首个 tick 必画
    n_bands++;
}

void activity()
{
    last_activity = millis();
    if (!awake) {                 // 活动即唤醒（下一 tick 全帧重画）
        awake = true;
        for (uint8_t i = 0; i < n_bands; i++) bands[i].dirty = true;
    }
}

void invalidate(uint8_t index)
{
    if (index >= n_bands) return;
    bands[index].dirty = true;
    activity();
}

void invalidateAll()
{
    for (uint8_t i = 0; i < n_bands; i++) bands[i].dirty = true;
    activity();
}

void setSleepTimeout(uint32_t ms)
{
    sleep_timeout = ms;
    if (awake) last_activity = millis();   // 防止设置瞬间立即息屏
}

void sleep()
{
    awake = false;
    pushBlackFrame();
    last_black = millis();
}

void wake()
{
    invalidateAll();
}

bool isAwake() { return awake; }

void tick()
{
    if (!online || n_bands == 0) return;
    uint32_t now = millis();

    if (!awake) {
        /* 息屏兜底：首帧传输偶发失败（共总线 NAK）时每 1s 重推全黑纠正 */
        if (now - last_black > 1000) {
            pushBlackFrame();
            last_black = now;
        }
        return;
    }

    /* 自动息屏判定：超时无任何活动 */
    if (sleep_timeout != 0 && now - last_activity > sleep_timeout) {
        sleep();
        return;
    }

    /* 扫描脏带 */
    bool any_static = false, any_live = false;
    for (uint8_t i = 0; i < n_bands; i++) {
        if (!bands[i].dirty) continue;
        if (bands[i].live) any_live = true;
        else               any_static = true;
    }

    /* 静态带脏 → 全帧重画（节流 100ms：连续参数变化只画最后一次） */
    if (any_static && now - last_full > 100) {
        u8g2.clearBuffer();
        for (uint8_t i = 0; i < n_bands; i++) {
            bands[i].draw(u8g2);
            bands[i].dirty = false;
        }
        u8g2.sendBuffer();
        last_full = now;
        last_live = now;             // 全帧已覆盖动态带，重置其节流
        return;
    }

    /* 仅动态带脏 → 逐带局部重发（每带 2 tile ≈ 256B ≈ 6ms，节流 200ms） */
    if (any_live && now - last_live > 200) {
        u8g2.setFont(u8g2_font_6x12_tf);
        for (uint8_t i = 0; i < n_bands; i++) {
            if (!bands[i].dirty || !bands[i].live) continue;
            u8g2.clearBuffer();
            bands[i].draw(u8g2);
            u8g2.updateDisplayArea(0, bands[i].y_tile, 16, bands[i].h_tile);
            bands[i].dirty = false;
        }
        last_live = now;
    }
}

} // namespace oled
