#ifndef TYPES_H
#define TYPES_H

#include <stdint.h>

typedef uint8_t  u8;
typedef int8_t   s8;
typedef uint16_t u16;
typedef int16_t  s16;

#define TRUE  1
#define FALSE 0

/* NES 畫面尺寸 */
#define SCREEN_W   256
#define SCREEN_H   240
#define PLAY_W     256
#define PLAY_H     208   /* 去掉 HUD 32px */

/* Sprite tile index（對應 CHR ROM 順序） */
#define SPR_BLANK      0
#define SPR_VF19_TOP   1
#define SPR_VF19_BOT   2
#define SPR_BULLET     3
#define SPR_EXP1       4
#define SPR_EXP2       5
#define SPR_ENEMY_A    6
#define SPR_ENEMY_B    7
#define SPR_SHIELD     8
#define SPR_NUM_0      12   /* 12~21 = 數字 0~9 */

/* 遊戲狀態 */
#define STATE_TITLE    0
#define STATE_GAME     1
#define STATE_BOSS     2
#define STATE_GAMEOVER 3
#define STATE_CLEAR    4

/* 最大物件數 */
#define MAX_BULLETS    8
#define MAX_ENEMIES   12
#define MAX_EBULLETS   8

#endif
