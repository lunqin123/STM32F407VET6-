/* ============================================================
 * Screen — OLED 公共显示层（SSD1306 128x64, 与 AS5600 共总线）
 *
 * 【为什么存在】三个实盘教训的统一解（2026-09-06 确诊）：
 *  1. 全帧 sendBuffer 1KB @400kHz 阻塞主循环 ~25-30ms，
 *     10Hz 定时刷新 = 含 move()/loopFOC() 的控制循环每秒被冻结 10 次
 *     → 电机肉眼可见顿挫，且提压无效极易误诊
 *     解：静态带事件驱动（参数变了才全帧），动态带局部 tile 重发（256B 级）
 *  2. u8g2.setPowerSave(1) 的 display-off 会冻结 I2C 总线，
 *     同总线的 AS5600 跟着死（转轴唤醒永远失效）
 *     解：息屏 = 推全黑帧 + 每 1s 兜底重推，总线保持活动
 *  3. 每个固件手写一遍屏幕逻辑，同一个坑反复踩
 *     解：统一入口 addBand / invalidate / tick / setSleepTimeout
 *
 * 【核心概念：带（band）】
 *   屏幕按 8x8 像素的 tile 划分（128x64 = 16x8 tiles）。
 *   一个 band = 若干连续 tile 行 + 绘制回调 + live 标志。
 *   - live=false（静态带）：标题/参数等，内容变了才重画，全帧发送
 *   - live=true （动态带）：实时角度/数值等，限速局部重发（~6ms/次）
 *
 * 【最小用法】
 *   setup:
 *     Wire.begin(); Wire.setClock(400000);   // 先启动总线
 *     oled::begin();
 *     oled::addBand(0, 2, false, drawTitle);   // 静态带
 *     oled::addBand(6, 2, true,  drawAngle);   // 动态带
 *     oled::setSleepTimeout(30000);            // 可选，0=禁用
 *   每个绘制回调只画自己带内的内容：
 *     void drawAngle(U8G2 &u) { u.drawStr(0, 62, "Ang 90.0 deg"); }
 *   loop:
 *     ...控制逻辑...
 *     oled::invalidate(1);      // 内容变了标脏（息屏时自动唤醒）
 *     oled::activity();         // 纯保活（不重画，如检测到转动）
 *     oled::tick();             // 每循环一次，内部非阻塞
 * ============================================================ */
#pragma once
#include <U8g2lib.h>
#include <Arduino.h>

namespace oled {

typedef void (*DrawFn)(U8G2 &u8g2);

bool begin();                        // OLED 不在线时返回 false（主功能不受影响）
void addBand(uint8_t y_tile, uint8_t h_tile, bool live, DrawFn draw);

void invalidate(uint8_t index);      // 标脏某带（息屏中会自动唤醒并重画）
void invalidateAll();                // 全部标脏（唤醒后/初始化后用）
void activity();                     // 纯保活：阻止自动息屏，不触发重画
void tick();                         // 每循环调用一次，内部非阻塞

void setSleepTimeout(uint32_t ms);   // 无活动超时自动息屏；0=禁用（默认 30s）
void sleep();                        // 手动息屏（推全黑帧）
void wake();                         // 手动唤醒（全帧重画）
bool isAwake();

} // namespace oled
