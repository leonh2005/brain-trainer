#ifndef BULLET_H
#define BULLET_H
#include "types.h"

typedef struct {
    u8  x, y;
    s8  vx, vy;
    u8  active;
    u8  homing;     /* 是否為導彈 */
    u8  homing_timer;
} Bullet;

typedef struct {
    u8  x, y;
    s8  vx, vy;
    u8  active;
} EBullet;

extern Bullet  bullets[MAX_BULLETS];
extern EBullet ebullets[MAX_EBULLETS];

void bullets_init(void);
void bullet_fire(u8 x, u8 y, s8 vx, s8 vy);
void bullet_fire_homing(u8 x, u8 y);
void bullets_update(void);
void bullets_draw(void);
void ebullet_fire(u8 x, u8 y, s8 vx, s8 vy);
void ebullets_update(void);
void ebullets_draw(void);
void ebullets_clear(void);

#endif
