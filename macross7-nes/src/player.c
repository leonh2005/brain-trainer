#include "player.h"
#include "bullet.h"
#include "neslib.h"

Player player;

/* NES pad 按鈕遮罩 */
#define PAD_A      0x01
#define PAD_B      0x02
#define PAD_SEL    0x04
#define PAD_START  0x08
#define PAD_UP     0x10
#define PAD_DOWN   0x20
#define PAD_LEFT   0x40
#define PAD_RIGHT  0x80

#define SPEED_NORM  2
#define SHOT_DELAY  8   /* 正常射擊冷卻 */
#define SHOT_RAPID  3   /* 速射冷卻 */

void player_init(void) {
    player.x          = 120;
    player.y          = 180;
    player.hp         = 5;
    player.speed      = SPEED_NORM;
    player.shot_cool  = 0;
    player.shield     = 0;
    player.rapid_timer= 0;
    player.homing     = 0;
    player.alive      = TRUE;
}

void player_update(u8 pad) {
    u8 spd = player.speed;

    /* 移動 */
    if ((pad & PAD_UP)    && player.y > 32)          player.y -= spd;
    if ((pad & PAD_DOWN)  && player.y < PLAY_H - 16) player.y += spd;
    if ((pad & PAD_LEFT)  && player.x > 8)           player.x -= spd;
    if ((pad & PAD_RIGHT) && player.x < SCREEN_W-16) player.x += spd;

    /* 射擊冷卻遞減 */
    if (player.shot_cool) --player.shot_cool;

    /* 道具計時 */
    if (player.rapid_timer) --player.rapid_timer;
    if (player.shield)      --player.shield;
    if (player.homing)      --player.homing;

    /* 按 A 射擊 */
    if ((pad & PAD_A) && !player.shot_cool) {
        u8 cool = player.rapid_timer ? SHOT_RAPID : SHOT_DELAY;
        if (player.homing)
            bullet_fire_homing(player.x + 4, player.y);
        else
            bullet_fire(player.x + 4, player.y, 0, -4);
        player.shot_cool = cool;
    }
}

void player_draw(void) {
    if (!player.alive) return;
    /* 上半機身 */
    oam_spr(player.x, player.y - 8, SPR_VF19_TOP, 0, 0);
    /* 下半機身 */
    oam_spr(player.x, player.y,     SPR_VF19_BOT, 0, 4);
    /* 護盾效果 */
    if (player.shield)
        oam_spr(player.x - 4, player.y - 12, SPR_SHIELD, 1, 8);
}

void player_hit(void) {
    if (player.shield) return;
    if (player.hp > 0) --player.hp;
    if (player.hp == 0) player.alive = FALSE;
}
