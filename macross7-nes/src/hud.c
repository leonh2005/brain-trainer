#include "hud.h"
#include "player.h"
#include "song.h"
#include "neslib.h"

void hud_draw(u8 stage, u16 score) {
    u8 i;
    /* HP 顯示（最多5格） */
    for (i = 0; i < 5; ++i) {
        u8 tile = (i < player.hp) ? SPR_VF19_TOP : SPR_BLANK;
        oam_spr(8 + i * 10, 228, tile, 0, 124 + i * 4);
    }
    /* 歌曲能量條 */
    song_draw_hud();
    /* 關卡顯示 */
    oam_spr(220, 228, SPR_NUM_0 + stage, 1, 144);
}
