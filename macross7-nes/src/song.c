#include "song.h"
#include "player.h"
#include "bullet.h"
#include "enemy.h"
#include "neslib.h"

SongSystem song;

void song_init(void) {
    song.energy      = 0;
    song.active_song = SONG_SEVENTH;
    song.effect_timer= 0;
}

void song_add_energy(u8 amt) {
    u16 e = song.energy + amt;
    song.energy = (e > SONG_MAX_ENERGY) ? SONG_MAX_ENERGY : (u8)e;
}

/* B 鍵循環切換歌曲 */
void song_select_next(void) {
    song.active_song++;
    if (song.active_song > SONG_DYNAMO)
        song.active_song = SONG_SEVENTH;
}

/* START 鍵發動（需 energy == 100） */
void song_activate(void) {
    u8 i;
    if (song.energy < SONG_MAX_ENERGY) return;
    song.energy = 0;

    switch (song.active_song) {
        case SONG_SEVENTH:
            /* 發射 8 枚導彈 */
            for (i = 0; i < 8; ++i)
                bullet_fire_homing(player.x + 4, player.y - i * 4);
            player.homing = 180;
            break;

        case SONG_TRY:
            /* 速射強化 180 幀 */
            player.rapid_timer = 180;
            break;

        case SONG_SOUL:
            /* 護盾 300 幀 */
            player.shield = 255;  /* 8bit 最大，約 255 幀 */
            break;

        case SONG_DYNAMO:
            /* 全螢幕炸彈：消滅所有敵人子彈 & 造成傷害 */
            enemies_bomb_all();
            break;
    }
    song.effect_timer = 30;  /* 發動動畫持續幀數 */
}

void song_update(void) {
    if (song.effect_timer) --song.effect_timer;
}

/* 在 HUD 區域畫出能量條和歌曲名 */
void song_draw_hud(void) {
    /* 能量條（最多 20 格，每格代表 5 點） */
    u8 bars = song.energy / 5;
    u8 i;
    for (i = 0; i < 20; ++i) {
        u8 tile = (i < bars) ? SPR_SHIELD : SPR_BLANK;
        oam_spr(8 + i * 6, 220, tile, i < bars ? 2 : 0, 148 + i * 4);
    }
}
