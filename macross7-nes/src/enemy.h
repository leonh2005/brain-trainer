#ifndef ENEMY_H
#define ENEMY_H
#include "types.h"

/* 敵人類型 */
#define ETYPE_NONE    0
#define ETYPE_A       1   /* Varauta 輕型 */
#define ETYPE_B       2   /* Varauta 重型 */
#define ETYPE_GIGIL   10  /* Boss 1 */
#define ETYPE_GAVIL   11  /* Boss 2 */
#define ETYPE_SIVIL   12  /* Boss 3 */
#define ETYPE_GLAVIL  13  /* Boss 4 */
#define ETYPE_GEPERU  14  /* Boss 5 最終 */

typedef struct {
    u8  x, y;
    u8  type;
    u8  hp;
    u8  active;
    u8  shot_cool;
    u8  phase;       /* Boss 攻擊階段 */
    u8  timer;
    s8  vx, vy;
} Enemy;

extern Enemy enemies[MAX_ENEMIES];

void  enemies_init(void);
void  enemies_update(void);
void  enemies_draw(void);
void  enemy_spawn(u8 x, u8 y, u8 type, u8 hp);
void  enemies_bomb_all(void);  /* Dynamite 全炸 */
u8    enemies_all_dead(void);

#endif
