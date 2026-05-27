#ifndef SONG_H
#define SONG_H
#include "types.h"

/* 歌曲 ID */
#define SONG_NONE     0
#define SONG_SEVENTH  1   /* Seventh Moon  → 導彈 (homing x8) */
#define SONG_TRY      2   /* Try Again     → 速射 180f */
#define SONG_SOUL     3   /* My Soul       → 護盾 300f */
#define SONG_DYNAMO   4   /* Dynamite Exp  → 全螢幕炸彈 */

#define SONG_MAX_ENERGY 100

typedef struct {
    u8 energy;      /* 0~100 */
    u8 active_song; /* 目前選中 */
    u8 effect_timer;
} SongSystem;

extern SongSystem song;

void song_init(void);
void song_add_energy(u8 amt);
void song_select_next(void);
void song_activate(void);
void song_update(void);
void song_draw_hud(void);

#endif
