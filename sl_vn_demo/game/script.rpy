## 獨自覺醒 — Demo 劇本
## 第一章：覺醒

# ══════════════════════════════════════════════
# 開始
# ══════════════════════════════════════════════

label start:

    # ── 模式選擇 ──
    menu:
        "【模式選擇】"
        "正常遊玩":
            pass
        "開發者模式（解鎖測試成人內容）":
            $ haein_affection = 20
            $ met_haein = True

    # ── 名稱輸入 ──
    $ player_name = renpy.input(
        "輸入主角名稱", default="金志遠", length=10
    ).strip() or "金志遠"

    # ── 序章 ──
    scene bg_black with fade
    pause 0.5

    "五年前，世界改變了。"
    pause 0.3
    "天空中出現了第一道「裂縫」——一道通往異空間的門。"
    "從那之後，怪物開始從裂縫湧出，攻擊人類城市。"
    pause 0.3
    "然而人類中有少數人——在接觸裂縫之後「覺醒」了。"
    "他們擁有超凡的戰鬥力，被稱為——「獵人」。"
    pause 0.4
    "獵人依戰力分為七個等級：S、A、B、C、D、E、F。"
    "S 級是人類的頂點，能以一人之力清除大型裂縫。"
    "而 E 級——"
    pause 0.3
    "是最底層的存在。"
    pause 0.5
    "我，就是 E 級獵人。"

    # ── 場景：獵人協會大廳 ──
    scene bg_hunter_hall with fade

    show mc_back at truecenter

    mc "（又是些沒人要接的爛任務……）"
    "任務佈告欄前，我一張一張翻看著剩下的任務卡。"

    hide mc_back

    npc "哎，E 級廢物也來看任務板？"
    npc "別擋道，讓真正的獵人過。"
    "旁邊傳來幾聲嘲笑。"
    "我沒有回頭。"
    mc "（……已經習慣了。）"

    "最後，我取下了一張任務卡。"
    mc "（D 級地下城清掃——比 E 級報酬高一倍。超出等級，但……）"
    mc "（今天不接就沒錢付房租。）"

    # ── 場景：地下城入口 ──
    scene bg_dungeon_entrance with fade
    "首爾郊區，D 級裂縫入口。"
    mc "（……進去吧。）"

    # ── 場景：地下城內部 ──
    scene bg_dungeon_corridor with fade
    "潮濕的石壁，腐臭的氣味，偶爾的低鳴。"
    "對 E 級獵人來說，這裡理論上是死地。"
    "但不知為何——我感覺不到恐懼。"

    "前方，出現了怪物的身影。"

    call battle_encounter("地下城地精", 30, 8) from _call_b1

    if not battle_won:
        jump game_over

    mc "（……比想像中弱。還是說——是我變強了？）"
    "繼續深入。"

    # ── 地下城深處 ──
    scene bg_dungeon_deep with fade
    "地下城最深處。路線在這裡中斷了——"
    "牆壁上，有一道隱約發光的縫隙。"
    mc "（……隱藏入口？）"

    menu:
        "要進去嗎？"
        "進入隱藏空間":
            call label_hidden_dungeon from _call_hidden
        "放棄，離開":
            mc "（……今天不冒這個險。）"

    jump label_exit_dungeon


label label_hidden_dungeon:
    scene bg_hidden_room with fade
    "空間不大，空氣中瀰漫著古老的魔力氣息。"
    "正中央，有一道光柱靜靜燃燒著。"
    mc "（這是……什麼？）"
    "我靠近光柱的瞬間——"

    scene bg_black with flash
    pause 0.3

    system "……偵測到適格者。"
    pause 0.5
    system "正在初始化——『玩家』系統。"
    pause 0.8
    system "初始化完成。"
    system "你是唯一的玩家。"
    system "成長。生存。超越一切。"

    $ awakened = True

    scene bg_hidden_room with fade
    mc "（……這是在做夢嗎？）"
    "手背上，多了一個淡淡發光的符文。"
    return


label label_exit_dungeon:
    scene bg_dungeon_entrance with fade
    "走出地下城——"
    "一個聲音叫住了我。"

    show haein neutral at right with moveinright

    haein "……等一下。"
    "眼前站著一名女性。"
    "白金色的短髮，腰間一把長劍。眼睛是罕見的金色。"

    show haein curious at right

    haein "你是獨自進入這座地下城的？"
    mc "對。"
    haein "E 級獵人……而且你身上的氣息很奇怪。"
    mc "氣息？"
    haein "我有感知獵人魔力的能力。但你的——不是普通 E 級的氣息。"

    mc "（……她是車海仁。S 級獵人。就連我這種 E 級也知道她的名字。）"

    haein "叫什麼名字？"
    mc "[player_name]。"
    haein "……[player_name]。記住了。"

    "她轉身走開。但在消失之前，她回頭看了我一眼。"
    "那個眼神，讓我有種說不清的感覺。"

    hide haein neutral with moveoutright

    $ met_haein = True

    mc "（……車海仁。系統覺醒。今天的事——都不像現實。）"

    jump day_start


# ══════════════════════════════════════════════
# 日常系統
# ══════════════════════════════════════════════

label day_start:
    scene bg_apartment with fade

    if day == 1:
        "回到安全屋。腦中的系統視窗不斷閃爍。"
        call screen system_window("系統初次啟動",
            "每日 AP 已重置：[ap] / [max_ap]\n\nAP 是你每天的行動力。\n請謹慎分配你的時間。")
        mc "（……不是夢。確實發生了。）"
        mc "（那麼，今天要怎麼用這些時間？）"
    else:
        "Day [day]，早晨。"
        call screen system_window("新的一天", "AP 已重置：[ap] / [max_ap]")

    jump ap_menu


label ap_menu:
    call screen ap_selection_screen

    if _return == "train":
        jump activity_train
    elif _return == "talk_haein":
        jump activity_talk_haein
    elif _return == "dungeon":
        jump activity_dungeon
    elif _return == "rest":
        jump activity_rest
    elif _return == "next_day":
        jump end_of_day

    jump ap_menu  # fallback


# ── 特訓 ──
label activity_train:
    scene bg_training_ground with fade
    "訓練場。"
    mc "（開始吧。）"
    "揮劍、體能、魔力控制——"
    "在系統的即時反饋下，每一個動作都比上一次更精準。"

    $ stat_str += 1
    $ stat_agi += 1
    $ ap -= 2

    call screen system_window("特訓完成", "STR +1　AGI +1\n\nAP 消耗：2")
    mc "（……確實有在進步。）"
    jump ap_menu


# ── 與車海仁交談 ──
label activity_talk_haein:
    scene bg_cafe with fade

    if haein_affection < 5:
        "在獵人協會的咖啡廳，偶然碰上了車海仁。"
        "她一個人坐在角落，面前一杯未動的咖啡。"
        show haein neutral at right

        mc "……這裡有人嗎？"
        haein "……坐吧。"
        "氣氛沉默了一會兒。"
        haein "你今天也去地下城了？"
        mc "是，需要任務費。E 級報酬太低，只能靠量來補。"
        show haein curious at right
        haein "……你身上的氣息，今天比第一次見面更強了。"
        mc "你還記得我？"
        haein "特殊的氣息不容易忘記。"
        "她的目光落在我手背的符文上。"
        haein "那個符文——是什麼？"
        mc "……我也還在弄清楚。"
        haein "……"
        $ haein_affection += 3
        $ ap -= 2
        call screen system_window("與車海仁交談", "好感度 +3（目前：[haein_affection]）\nAP 消耗：2")

    elif haein_affection < 10:
        "主動約了車海仁吃午餐。意外的是——她答應了。"
        show haein neutral at right
        haein "你為什麼約我？"
        mc "想多了解你一點。"
        haein "……大膽。"
        mc "你平常喜歡做什麼？"
        show haein surprised at right
        haein "……貓。"
        mc "貓？"
        haein "我養了一隻。叫雪球。"
        mc "……完全想不到。"
        haein "獵人不能喜歡貓？"
        mc "不是，只是……感覺反差很——"
        show haein flustered at right
        haein "反差很什麼？"
        mc "……可愛。"
        haein "……"
        "她把視線移開了。但我看到她耳尖微微紅了。"
        $ haein_affection += 4
        $ ap -= 2
        call screen system_window("與車海仁交談", "好感度 +4（目前：[haein_affection]）\nAP 消耗：2")

    else:
        "最近和車海仁的關係明顯親近了不少。"
        show haein slight_smile at right
        haein "訓練有進展嗎？"
        mc "今天的數值又提升了。"
        haein "你的成長速度……說異常都不為過。"
        mc "只是順著系統走而已。"
        show haein curious at right
        haein "你說的系統——"
        mc "等級、技能、狀態視窗，全都有。像遊戲一樣。"
        haein "……"
        "她沉默了一陣。"
        haein "……我相信你。"
        mc "為什麼？"
        haein "你的氣息，和系統覺醒的理論吻合。"
        show haein serious at right
        haein "[player_name]……要小心。有人不希望出現超出等級限制的獵人。"
        mc "……我知道。"
        $ haein_affection += 5
        $ ap -= 2
        call screen system_window("與車海仁交談", "好感度 +5（目前：[haein_affection]）\nAP 消耗：2")

        if haein_affection >= 15 and not haein_scene1_done:
            hide haein serious
            jump haein_scene1

    hide haein neutral
    jump ap_menu


# ── 進入地下城 ──
label activity_dungeon:
    scene bg_dungeon_entrance with fade
    "前往附近的 E 級地下城。"

    call battle_encounter("地下城骷髏", 50, 10) from _call_b2

    if battle_won:
        $ exp_gain = 30 + stat_str * 2
        $ player_exp += exp_gain
        $ ap -= 3
        call screen system_window("地下城完成",
            "EXP +" + str(exp_gain) + "\n（目前 EXP：" + str(player_exp) + " / " + str(100 * player_level) + "）\n\nAP 消耗：3")

        if player_exp >= 100 * player_level:
            $ player_level += 1
            $ player_exp = player_exp - 100 * (player_level - 1)
            $ player_max_hp += 20
            $ player_max_mp += 10
            $ player_hp = player_max_hp
            $ player_mp = player_max_mp
            call screen system_window("等級提升！",
                "Level [player_level]！\n\nHP 上限 +20　MP 上限 +10")
    else:
        mc "（……今天狀態不好，先撤退。）"
        $ ap -= 2

    jump ap_menu


# ── 休息 ──
label activity_rest:
    scene bg_apartment with fade
    mc "（今天就到這裡吧。）"
    $ player_hp = player_max_hp
    $ player_mp = player_max_mp
    $ ap -= 1
    call screen system_window("休息完成", "HP / MP 完全恢復\n\nAP 消耗：1")
    jump ap_menu


# ── 一天結束 ──
label end_of_day:
    scene bg_apartment with fade
    "深夜。"
    mc "（今天也結束了。）"
    $ day += 1
    $ ap = max_ap

    if day <= 3:
        jump day_start
    else:
        jump chapter1_end


# ══════════════════════════════════════════════
# 車海仁場景
# ══════════════════════════════════════════════

label haein_scene1:
    $ haein_scene1_done = True

    scene bg_apartment_night with fade

    "深夜，安全屋。"
    "今天一起完成了一個臨時升級的 C 級任務。"
    "兩人都帶著傷。"

    show haein neutral at right

    haein "……讓我看傷口。"
    mc "不用，不深——"
    haein "讓我看。"
    "她的語氣沒有商量的餘地。"
    "我伸出了手臂。"

    show haein serious at right

    "她輕柔地處理著傷口。"
    "在這個距離，我能聞到她的氣息——清淡的，帶著某種讓人靜下來的香氣。"

    mc "……你也受傷了。"
    haein "皮肉傷，會自癒的。"
    mc "讓我來。"

    show haein surprised at right

    haein "……"
    "她沒有拒絕。"
    "我幫她包紮的時候，兩人都沒有說話。"
    "窗外的首爾燈火通明，但這個小空間裡，一切都很安靜。"

    mc "……好了。"
    "我抬起頭——她的臉近在咫尺。"
    "金色的眼睛直視著我。"

    show haein flustered at right

    haein "……[player_name]。"
    mc "嗯？"
    haein "你的氣息……最近讓我很難集中。"
    mc "……怎麼說？"
    haein "說不清楚。我能感知所有獵人的魔力，但你的——不一樣。"
    haein "像是在吸引我。"
    "她說著，語氣裡帶著幾分困惑。"
    "平時冷靜自持的 S 級獵人，此刻的眼神有些迷失。"

    menu:
        mc "……（要怎麼回應？）"
        "靠近她":
            jump haein_scene1_adult
        "先退開":
            mc "……對不起。如果我讓你感到困擾——"
            haein "不。不是困擾。"
            show haein neutral at right
            haein "……只是不習慣這種感覺。"
            "那晚就這樣結束了。"
            "但之後——我們都清楚，有什麼東西已經開始改變了。"
            $ haein_affection += 2
            hide haein neutral
            jump ap_menu


label haein_scene1_adult:
    "我沒有後退。"
    "緩緩地，捧起她的臉。"

    show haein flustered at right

    haein "……你——"
    mc "我也一樣。你讓我很難靜下心來。"
    "她的眼睛微微睜大。"
    "然後——輕輕閉上了。"
    "第一次的吻，從輕觸開始，慢慢加深。"
    "她的手指找到了我的衣領，輕輕扣住。"

    scene bg_apartment_night with dissolve

    "窗外的城市依然喧囂。"
    "但在這個空間裡——一切都只有彼此。"
    "她的吻比外表更熱。"
    "冷靜的外殼之下，是壓抑已久的溫度。"

    show haein soft at center

    haein "……[player_name]。"
    mc "嗯。"
    haein "今晚……不要離開。"

    scene bg_black with dissolve

    "【CG：車海仁 Scene 01 — 待替換美術】"
    pause 0.5

    "她的髮絲在黑暗中散開，白金色映著窗外透進的月光。"
    pause 0.3
    "平日整齊的制服，安靜地落在地板上。"
    pause 0.3
    "她的肌膚在昏暗的光線下顯得格外白皙，"
    "頸項、鎖骨的線條，隨著她的呼吸輕輕起伏。"
    pause 0.3
    haein "……"
    "她不再說話，只是輕輕拉著我靠近。"
    "那雙平時握劍的手，此刻卻顫抖地扣著我的背。"
    pause 0.4
    "她低低地喚著我的名字——[player_name]、[player_name]——"
    "聲音不再是往日的冷靜，"
    "取而代之的，是真實的、屬於她的溫柔與渴望。"
    pause 0.5
    "那個夜晚，S 級獵人車海仁，"
    "和系統選中的唯一玩家——"
    "就這樣，安靜地交疊在一起。"

    scene bg_apartment_night_after with fade

    "黎明前。"
    "她靠在我肩膀上，睫毛輕輕顫著。"

    show haein soft at center

    haein "……不准告訴任何人。"
    mc "我知道。"
    haein "我是認真的。"
    mc "我也是。"
    "沉默了一會兒。"
    haein "……[player_name]。"
    mc "嗯。"
    haein "你的氣息……現在讓我很平靜。"
    mc "……我也是。"
    "她沒有再說話，只是將頭靠得更近了一些。"

    hide haein soft

    $ haein_affection += 10

    call screen system_window("〖車海仁〗關係進展",
        "關係：陌生人　→　情人（秘密）\n\n好感度大幅提升\n（目前：[haein_affection]）")

    scene bg_apartment with fade
    jump ap_menu


# ══════════════════════════════════════════════
# 第一章結尾
# ══════════════════════════════════════════════

label chapter1_end:
    scene bg_hunter_hall with fade
    "三天後。"
    "獵人協會公告欄上，出現了一則緊急通知。"
    "【S 級裂縫確認，首爾近郊。所有可用獵人即刻集結。】"
    mc "（S 級……）"

    show haein serious at right with moveinright

    haein "[player_name]。"
    mc "車海仁——你要去嗎？"
    haein "這是我的職責。"

    show haein worried at right

    haein "……你呢？"
    mc "系統給了我一個任務標記。這個裂縫——和我有關。"
    haein "有關……怎麼說？"
    mc "不知道。但系統說，這個裂縫是為我準備的。"
    "她沉默了一下，目光複雜。"
    haein "……跟緊我。不准死。"
    mc "好。"

    hide haein worried with moveoutright

    scene bg_black with fade

    centered "第一章　終"
    pause 1.0
    centered "Chapter 2 ——《暗影覺醒》　即將推出"
    pause 1.0

    scene bg_black with fade

    "【Demo v0.1 結束】"
    pause 0.3
    "感謝遊玩。"
    pause 0.5
    "完整版預計內容："
    "・三位主要攻略角色"
    "・四語言支援（繁中 / 簡中 / 英文 / 日文）"
    "・完整 AP 養成系統與多結局"
    "・4.5 小時以上主線劇情"

    return


# ══════════════════════════════════════════════
# 遊戲結束
# ══════════════════════════════════════════════

label game_over:
    scene bg_black with fade
    "戰鬥失敗。"
    menu:
        "【GAME OVER】"
        "重新開始":
            jump start


# ══════════════════════════════════════════════
# 戰鬥系統
# ══════════════════════════════════════════════

label battle_encounter(enemy_name, enemy_max_hp, enemy_atk):

    $ battle_enemy_name = enemy_name
    $ battle_enemy_max_hp = enemy_max_hp
    $ battle_enemy_hp = enemy_max_hp
    $ battle_player_hp = player_max_hp
    $ battle_player_mp = player_max_mp
    $ battle_won = False
    $ battle_ongoing = True

    "【戰鬥開始】[battle_enemy_name]　HP [battle_enemy_hp] / [battle_enemy_max_hp]"

    while battle_ongoing:

        $ _dmg_n = max(1, stat_str + renpy.random.randint(-2, 3))
        $ _dmg_s = max(1, stat_str * 2 + renpy.random.randint(-3, 5))
        $ _edamage = max(1, enemy_atk + renpy.random.randint(-2, 2))

        menu:
            "HP [battle_player_hp]/[player_max_hp]  MP [battle_player_mp]/[player_max_mp]  ──  [battle_enemy_name] HP [battle_enemy_hp]/[battle_enemy_max_hp]"

            "普通攻擊　（預估傷害 [_dmg_n]）":
                $ battle_enemy_hp = max(0, battle_enemy_hp - _dmg_n)
                "對 [battle_enemy_name] 造成 [_dmg_n] 點傷害！"
                if battle_enemy_hp <= 0:
                    $ battle_ongoing = False
                    $ battle_won = True
                else:
                    $ battle_player_hp = max(0, battle_player_hp - _edamage)
                    "[battle_enemy_name] 反擊！造成 [_edamage] 點傷害。"
                    if battle_player_hp <= 0:
                        $ battle_ongoing = False

            "影劍　（預估傷害 [_dmg_s]，消耗 10 MP）" if battle_player_mp >= 10:
                $ battle_player_mp -= 10
                $ battle_enemy_hp = max(0, battle_enemy_hp - _dmg_s)
                "影劍貫穿！造成 [_dmg_s] 點傷害！"
                if battle_enemy_hp <= 0:
                    $ battle_ongoing = False
                    $ battle_won = True
                else:
                    $ battle_player_hp = max(0, battle_player_hp - _edamage)
                    "[battle_enemy_name] 反擊！造成 [_edamage] 點傷害。"
                    if battle_player_hp <= 0:
                        $ battle_ongoing = False

            "撤退":
                mc "（……先撤。）"
                $ battle_ongoing = False

    if battle_won:
        "【勝利】[battle_enemy_name] 被擊敗了！"
    elif battle_player_hp <= 0:
        "……你倒下了。"

    return
