#include "scroll.h"
#include "neslib.h"

static u8 scroll_y;
static u8 scroll_speed;  /* 每幀捲動像素數 */

void scroll_init(void) {
    scroll_y     = 0;
    scroll_speed = 1;
}

void scroll_update(void) {
    scroll_y += scroll_speed;
    /* NES 垂直捲軸：設定 PPU scroll */
    scroll(0, scroll_y);
}

u8 scroll_get_y(void) {
    return scroll_y;
}
