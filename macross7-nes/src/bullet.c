#include "bullet.h"
#include "player.h"
#include "neslib.h"

Bullet  bullets[MAX_BULLETS];
EBullet ebullets[MAX_EBULLETS];

void bullets_init(void) {
    u8 i;
    for (i = 0; i < MAX_BULLETS;  ++i) bullets[i].active  = FALSE;
    for (i = 0; i < MAX_EBULLETS; ++i) ebullets[i].active = FALSE;
}

void bullet_fire(u8 x, u8 y, s8 vx, s8 vy) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; ++i) {
        if (!bullets[i].active) {
            bullets[i].x      = x;
            bullets[i].y      = y;
            bullets[i].vx     = vx;
            bullets[i].vy     = vy;
            bullets[i].homing = FALSE;
            bullets[i].active = TRUE;
            return;
        }
    }
}

void bullet_fire_homing(u8 x, u8 y) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; ++i) {
        if (!bullets[i].active) {
            bullets[i].x            = x;
            bullets[i].y            = y;
            bullets[i].vx           = 0;
            bullets[i].vy           = -3;
            bullets[i].homing       = TRUE;
            bullets[i].homing_timer = 60;
            bullets[i].active       = TRUE;
            return;
        }
    }
}

void bullets_update(void) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; ++i) {
        if (!bullets[i].active) continue;
        bullets[i].x += bullets[i].vx;
        bullets[i].y += bullets[i].vy;
        /* 飛出畫面則消滅 */
        if (bullets[i].y < 8 || bullets[i].y > 240 ||
            bullets[i].x < 0 || bullets[i].x > 248) {
            bullets[i].active = FALSE;
        }
    }
}

void bullets_draw(void) {
    u8 i;
    for (i = 0; i < MAX_BULLETS; ++i) {
        if (!bullets[i].active) continue;
        oam_spr(bullets[i].x, bullets[i].y, SPR_BULLET, 1, 12 + i * 4);
    }
}

void ebullet_fire(u8 x, u8 y, s8 vx, s8 vy) {
    u8 i;
    for (i = 0; i < MAX_EBULLETS; ++i) {
        if (!ebullets[i].active) {
            ebullets[i].x      = x;
            ebullets[i].y      = y;
            ebullets[i].vx     = vx;
            ebullets[i].vy     = vy;
            ebullets[i].active = TRUE;
            return;
        }
    }
}

void ebullets_update(void) {
    u8 i;
    for (i = 0; i < MAX_EBULLETS; ++i) {
        if (!ebullets[i].active) continue;
        ebullets[i].x += ebullets[i].vx;
        ebullets[i].y += ebullets[i].vy;
        if (ebullets[i].y > 240 || ebullets[i].x > 248)
            ebullets[i].active = FALSE;
    }
}

void ebullets_draw(void) {
    u8 i;
    for (i = 0; i < MAX_EBULLETS; ++i) {
        if (!ebullets[i].active) continue;
        oam_spr(ebullets[i].x, ebullets[i].y, SPR_BULLET, 3, 44 + i * 4);
    }
}

void ebullets_clear(void) {
    u8 i;
    for (i = 0; i < MAX_EBULLETS; ++i) ebullets[i].active = FALSE;
}
