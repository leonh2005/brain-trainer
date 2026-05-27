/*
 * 超時空要塞7: Sound Force
 * NES 垂直捲軸射擊遊戲（TwinBee 風格）
 * Platform : Nintendo Family Computer
 * Toolchain: cc65 + NESlib
 */

#include "neslib.h"
#include "types.h"
#include "player.h"
#include "bullet.h"
#include "enemy.h"
#include "song.h"
#include "scroll.h"
#include "hud.h"

/* ── 調色盤 ───────────────────────────────────────────────── */
static const unsigned char palette[32] = {
    /* BG 調色盤 */
    0x0F,0x01,0x21,0x31,   /* 深空藍系 */
    0x0F,0x09,0x19,0x29,   /* 雲層灰白 */
    0x0F,0x06,0x16,0x26,   /* 地面棕色 */
    0x0F,0x00,0x10,0x30,   /* 星點白 */
    /* Sprite 調色盤 */
    0x0F,0x16,0x27,0x37,   /* VF-19 紅橙 */
    0x0F,0x02,0x12,0x22,   /* 藍色子彈 */
    0x0F,0x08,0x18,0x28,   /* 敵機 */
    0x0F,0x19,0x29,0x39,   /* Boss */
};

/* ── 關卡波次表 ───────────────────────────────────────────── */
#define MAX_STAGES 5

/* 每關 Boss 類型 */
static const u8 boss_type[MAX_STAGES] = {
    ETYPE_GIGIL, ETYPE_GAVIL, ETYPE_SIVIL, ETYPE_GLAVIL, ETYPE_GEPERU
};

/* 每關 Boss HP */
static const u8 boss_hp[MAX_STAGES] = { 8, 12, 15, 20, 30 };

/* ── 全域遊戲狀態 ─────────────────────────────────────────── */
static u8  game_state;
static u8  stage;           /* 0~4 */
static u16 score;
static u16 frame;
static u8  boss_spawned;
static u8  spawn_timer;

/* ── 碰撞偵測（AABB，8x8） ───────────────────────────────── */
static u8 collide(u8 ax, u8 ay, u8 bx, u8 by) {
    if (ax + 7 < bx || bx + 7 < ax) return FALSE;
    if (ay + 7 < by || by + 7 < ay) return FALSE;
    return TRUE;
}

/* ── 波次生成 ─────────────────────────────────────────────── */
static void spawn_wave(void) {
    u8 pattern;
    if (spawn_timer) { --spawn_timer; return; }

    /* 根據幀數決定生成模式 */
    pattern = (frame / 200) % 4;
    switch (pattern) {
        case 0: /* V 字三機 */
            enemy_spawn(80,  0, ETYPE_A, 2);
            enemy_spawn(120, 0, ETYPE_A, 2);
            enemy_spawn(160, 0, ETYPE_A, 2);
            break;
        case 1: /* 左右夾擊 */
            enemy_spawn(20,  0, ETYPE_B, 4);
            enemy_spawn(220, 0, ETYPE_B, 4);
            break;
        case 2: /* 橫排五機 */
            { u8 i; for (i=0;i<5;++i) enemy_spawn(30+i*40, 0, ETYPE_A, 2); }
            break;
        case 3: /* 對角線 */
            enemy_spawn(10,  0, ETYPE_A, 3);
            enemy_spawn(50,  20, ETYPE_A, 3);
            enemy_spawn(90,  40, ETYPE_A, 3);
            break;
    }
    spawn_timer = 120;
}

/* ── 碰撞處理 ─────────────────────────────────────────────── */
static void check_collisions(void) {
    u8 i, j;

    /* 玩家子彈 vs 敵人 */
    for (i = 0; i < MAX_BULLETS; ++i) {
        if (!bullets[i].active) continue;
        for (j = 0; j < MAX_ENEMIES; ++j) {
            if (!enemies[j].active) continue;
            if (collide(bullets[i].x, bullets[i].y,
                        enemies[j].x, enemies[j].y)) {
                bullets[i].active = FALSE;
                if (enemies[j].hp > 0) --enemies[j].hp;
                if (enemies[j].hp == 0) {
                    enemies[j].active = FALSE;
                    score += (enemies[j].type < 10) ? 100 : 1000;
                    song_add_energy(enemies[j].type < 10 ? 8 : 20);
                }
            }
        }
    }

    /* 敵人子彈 vs 玩家 */
    if (!player.shield) {
        for (i = 0; i < MAX_EBULLETS; ++i) {
            if (!ebullets[i].active) continue;
            if (collide(ebullets[i].x, ebullets[i].y,
                        player.x, player.y)) {
                ebullets[i].active = FALSE;
                player_hit();
            }
        }
    }

    /* 敵機 vs 玩家 */
    for (j = 0; j < MAX_ENEMIES; ++j) {
        if (!enemies[j].active) continue;
        if (collide(enemies[j].x, enemies[j].y, player.x, player.y)) {
            player_hit();
            if (enemies[j].type < 10) enemies[j].active = FALSE;
        }
    }
}

/* ── 遊戲初始化 ───────────────────────────────────────────── */
static void game_init(void) {
    player_init();
    bullets_init();
    enemies_init();
    song_init();
    scroll_init();
    stage        = 0;
    score        = 0;
    frame        = 0;
    boss_spawned = FALSE;
    spawn_timer  = 60;
    game_state   = STATE_GAME;
}

/* ── 主遊戲邏輯（每幀）─────────────────────────────────────── */
static void game_update(void) {
    u8 pad = pad_poll(0);

    player_update(pad);

    /* B 切歌，START 發動 */
    if (pad & PAD_B)     song_select_next();
    if (pad & PAD_START) song_activate();

    song_update();

    /* 關卡流程：幀數 > 800 且敵人清空 → 生成 Boss */
    if (!boss_spawned && frame > 800 && enemies_all_dead()) {
        enemy_spawn(100, 20, boss_type[stage], boss_hp[stage]);
        boss_spawned = TRUE;
        game_state   = STATE_BOSS;
    }

    /* Boss 已死 → 進下一關或通關 */
    if (game_state == STATE_BOSS && enemies_all_dead()) {
        if (stage < MAX_STAGES - 1) {
            ++stage;
            boss_spawned = FALSE;
            frame        = 0;
            spawn_timer  = 60;
            enemies_init();
            bullets_init();
            ebullets_clear();
            game_state = STATE_GAME;
        } else {
            game_state = STATE_CLEAR;
        }
    }

    if (game_state == STATE_GAME)
        spawn_wave();

    bullets_update();
    ebullets_update();
    enemies_update();
    check_collisions();
    scroll_update();

    if (!player.alive) game_state = STATE_GAMEOVER;
    ++frame;
}

/* ── 主程式入口 ───────────────────────────────────────────── */
void main(void) {
    pal_all(palette);
    oam_clear();
    ppu_on_all();
    game_init();

    while (1) {
        ppu_wait_nmi();  /* 等待 VBlank */
        oam_clear();

        switch (game_state) {
            case STATE_GAME:
            case STATE_BOSS:
                game_update();
                player_draw();
                bullets_draw();
                ebullets_draw();
                enemies_draw();
                hud_draw(stage + 1, score);
                break;

            case STATE_GAMEOVER:
                /* 顯示 GAME OVER，按 START 重開 */
                if (pad_poll(0) & PAD_START) game_init();
                break;

            case STATE_CLEAR:
                /* 顯示通關，按 START 重開 */
                if (pad_poll(0) & PAD_START) game_init();
                break;
        }
    }
}
