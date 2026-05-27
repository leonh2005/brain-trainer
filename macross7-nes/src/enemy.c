#include "enemy.h"
#include "bullet.h"
#include "player.h"
#include "song.h"
#include "neslib.h"

Enemy enemies[MAX_ENEMIES];

void enemies_init(void) {
    u8 i;
    for (i = 0; i < MAX_ENEMIES; ++i) enemies[i].active = FALSE;
}

void enemy_spawn(u8 x, u8 y, u8 type, u8 hp) {
    u8 i;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (!enemies[i].active) {
            enemies[i].x         = x;
            enemies[i].y         = y;
            enemies[i].type      = type;
            enemies[i].hp        = hp;
            enemies[i].active    = TRUE;
            enemies[i].shot_cool = 60;
            enemies[i].phase     = 0;
            enemies[i].timer     = 0;
            enemies[i].vx        = 0;
            enemies[i].vy        = (type < 10) ? 1 : 0;  /* 雜兵向下移動 */
            return;
        }
    }
}

/* ── 雜兵 AI ──────────────────────────────────────────────── */
static void update_minion(Enemy *e) {
    e->x += e->vx;
    e->y += e->vy;
    /* 飛出畫面 */
    if (e->y > 240) { e->active = FALSE; return; }
    /* 射擊 */
    if (e->shot_cool) { --e->shot_cool; return; }
    ebullet_fire(e->x + 4, e->y + 8, 0, 2);
    e->shot_cool = 80;
}

/* ── Boss 1: Gigil ─────────────────────────────────────────── */
static void update_gigil(Enemy *e) {
    ++e->timer;
    /* 左右搖擺 */
    if (e->timer & 0x40) e->x = (e->x < 200) ? e->x + 1 : e->x;
    else                 e->x = (e->x > 40)  ? e->x - 1 : e->x;
    /* 3-way shot */
    if (!e->shot_cool) {
        ebullet_fire(e->x,      e->y + 16, -1, 2);
        ebullet_fire(e->x + 8,  e->y + 16,  0, 2);
        ebullet_fire(e->x + 16, e->y + 16,  1, 2);
        e->shot_cool = 50;
    } else --e->shot_cool;
}

/* ── Boss 2: Gavil ─────────────────────────────────────────── */
static void update_gavil(Enemy *e) {
    ++e->timer;
    /* 快速 Z 字移動 */
    if (e->timer % 30 == 0) {
        e->vx = (e->vx == 2) ? -2 : 2;
    }
    e->x += e->vx;
    if (e->x > 220) e->x = 220;
    if (e->x < 20)  e->x = 20;
    /* 5-way 扇形 */
    if (!e->shot_cool) {
        s8 i;
        for (i = -2; i <= 2; ++i)
            ebullet_fire(e->x + 8, e->y + 16, i, 2);
        e->shot_cool = 40;
    } else --e->shot_cool;
}

/* ── Boss 3: Sivil ─────────────────────────────────────────── */
static void update_sivil(Enemy *e) {
    ++e->timer;
    /* 慢速圓形移動 */
    e->x = 120 + (s8)(60 * (e->timer & 0x80 ? -1 : 1));
    /* 360° 彈幕（8方向） */
    if (!e->shot_cool) {
        static s8 dx[] = {0,1,1,1,0,-1,-1,-1};
        static s8 dy[] = {-2,-2,0,2,2,2,0,-2};
        u8 j;
        for (j = 0; j < 8; ++j)
            ebullet_fire(e->x + 8, e->y + 8, dx[j], dy[j]);
        e->shot_cool = 60;
    } else --e->shot_cool;
}

/* ── Boss 4: Glavil ────────────────────────────────────────── */
static void update_glavil(Enemy *e) {
    ++e->timer;
    /* 高速衝撞 */
    if (e->phase == 0) {
        if (e->timer > 60) { e->vy = 3; e->phase = 1; }
    } else if (e->phase == 1) {
        e->y += e->vy;
        if (e->y > 180) { e->vy = -3; e->phase = 2; }
    } else {
        e->y += e->vy;
        if (e->y < 20)  { e->vy = 0; e->phase = 0; e->timer = 0; }
    }
    /* 密集彈幕 */
    if (!e->shot_cool) {
        u8 j;
        for (j = 0; j < 3; ++j)
            ebullet_fire(e->x + j*8, e->y + 16, (s8)(j-1), 3);
        e->shot_cool = 20;
    } else --e->shot_cool;
}

/* ── Boss 5: Geperuniti（最終）─────────────────────────────── */
static void update_geperu(Enemy *e) {
    ++e->timer;
    /* 兩階段：HP > 10 正常，HP ≤ 10 暴走 */
    if (e->hp > 10) {
        /* 橫向掃射 */
        if (e->timer % 2 == 0)
            e->x += (e->timer & 0x80) ? -1 : 1;
        if (!e->shot_cool) {
            u8 j;
            for (j = 0; j < 4; ++j)
                ebullet_fire(e->x + j*6, e->y + 16, (s8)(j-1), 2);
            e->shot_cool = 25;
        } else --e->shot_cool;
    } else {
        /* 暴走：全方向彈幕 */
        if (!e->shot_cool) {
            static s8 ddx[] = {0,1,1,1,0,-1,-1,-1,0};
            static s8 ddy[] = {-3,-2,-1,1,3,1,-1,-2,-3};
            u8 j;
            for (j = 0; j < 9; ++j)
                ebullet_fire(e->x + 8, e->y + 8, ddx[j], ddy[j]);
            e->shot_cool = 15;
        } else --e->shot_cool;
    }
}

void enemies_update(void) {
    u8 i;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (!enemies[i].active) continue;
        switch (enemies[i].type) {
            case ETYPE_A:
            case ETYPE_B:     update_minion(&enemies[i]);  break;
            case ETYPE_GIGIL: update_gigil(&enemies[i]);   break;
            case ETYPE_GAVIL: update_gavil(&enemies[i]);   break;
            case ETYPE_SIVIL: update_sivil(&enemies[i]);   break;
            case ETYPE_GLAVIL:update_glavil(&enemies[i]);  break;
            case ETYPE_GEPERU:update_geperu(&enemies[i]);  break;
        }
    }
}

void enemies_draw(void) {
    u8 i, tile;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (!enemies[i].active) continue;
        tile = (enemies[i].type < 10) ? SPR_ENEMY_A : SPR_ENEMY_B;
        oam_spr(enemies[i].x, enemies[i].y, tile, 2, 76 + i * 4);
    }
}

void enemies_bomb_all(void) {
    u8 i;
    ebullets_clear();
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (!enemies[i].active || enemies[i].type >= 10) continue;
        enemies[i].active = FALSE;
        song_add_energy(5);
    }
}

u8 enemies_all_dead(void) {
    u8 i;
    for (i = 0; i < MAX_ENEMIES; ++i)
        if (enemies[i].active) return FALSE;
    return TRUE;
}
