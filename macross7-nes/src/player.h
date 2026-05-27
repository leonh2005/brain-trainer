#ifndef PLAYER_H
#define PLAYER_H
#include "types.h"

typedef struct {
    u8 x, y;        /* 位置 */
    u8 hp;          /* 生命值（最大5） */
    u8 speed;       /* 移動速度 */
    u8 shot_cool;   /* 射擊冷卻 */
    u8 shield;      /* 護盾計時器 */
    u8 rapid_timer; /* 速射道具計時器 */
    u8 homing;      /* 導彈道具計時器 */
    u8 alive;
} Player;

extern Player player;

void player_init(void);
void player_update(u8 pad);
void player_draw(void);
void player_hit(void);

#endif
