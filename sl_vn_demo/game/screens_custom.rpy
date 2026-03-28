## 自訂畫面

# ── 覆蓋主選單（避免 gui.show_name 錯誤） ──
screen main_menu():
    tag menu

    add "#0A0A18"

    vbox:
        xalign 0.5
        yalign 0.45
        spacing 20

        text "獨自覺醒":
            color "#4FC3F7"
            size 52
            bold True
            xalign 0.5

        text "Demo v0.1":
            color "#76FF03"
            size 20
            xalign 0.5

        null height 30

        textbutton "開始遊戲":
            xalign 0.5
            xsize 280
            ypadding 12
            action Start()
            text_color "#FFFFFF"
            text_size 22
            background "#0D2030"
            hover_background "#1A4050"

        textbutton "讀取存檔":
            xalign 0.5
            xsize 280
            ypadding 12
            action ShowMenu("load")
            text_color "#CCCCCC"
            text_size 22
            background "#0D2030"
            hover_background "#1A4050"

        textbutton "退出":
            xalign 0.5
            xsize 280
            ypadding 12
            action Quit(confirm=False)
            text_color "#AAAAAA"
            text_size 22
            background "#111111"
            hover_background "#222222"


# ── 退出確認視窗 ──
screen confirm(message, yes_action, no_action=NullAction()):
    modal True
    zorder 300

    frame:
        xalign 0.5
        yalign 0.4
        xsize 480
        padding (30, 25)
        background "#111111DD"

        vbox:
            spacing 20
            text message color "#FFFFFF" size 20 xalign 0.5
            hbox:
                spacing 30
                xalign 0.5
                textbutton "確認":
                    action yes_action
                    text_color "#76FF03"
                    text_size 18
                    background "#0D2030"
                    hover_background "#1A4050"
                    xpadding 25
                    ypadding 10
                textbutton "取消":
                    action no_action
                    text_color "#AAAAAA"
                    text_size 18
                    background "#1A1A1A"
                    hover_background "#2A2A2A"
                    xpadding 25
                    ypadding 10


# ── 系統通知視窗 ──
screen system_window(title, content):
    modal True
    zorder 200

    frame:
        xalign 0.5
        yalign 0.45
        xsize 520
        padding (28, 22)
        background "#0B1A2CCC"

        vbox:
            spacing 14

            text title:
                color "#76FF03"
                size 21
                xalign 0.5
                bold True

            null height 4

            text content:
                color "#D8E8F0"
                size 17
                xalign 0.5
                line_leading 4

            null height 12

            textbutton "確認":
                xalign 0.5
                action Return()
                text_color "#76FF03"
                text_size 17
                background "#0D2030"
                hover_background "#1A3848"
                xpadding 30
                ypadding 8


# ── AP 選擇畫面 ──
screen ap_selection_screen():
    modal True
    zorder 100

    frame:
        xalign 0.5
        yalign 0.35
        xsize 620
        padding (28, 24)
        background "#0A1628F0"

        vbox:
            spacing 8

            hbox:
                spacing 30
                text "Day [day]"           color "#76FF03"  size 21 bold True
                text "AP  [ap] / [max_ap]" color "#4FC3F7"  size 21 bold True

            null height 8
            text "─────────────────────────────" color "#2A3A4A" size 15
            null height 6

            if ap >= 2:
                textbutton "⚔  特訓　　（消耗 2 AP）　　STR / AGI +1":
                    action Return("train")
                    text_color "#E0E8FF"
                    text_size 17
                    background "#0D2030"
                    hover_background "#1A3040"
                    xsize 568
                    ypadding 10
            else:
                text "⚔  特訓　　（AP 不足）" color "#404A50" size 17

            null height 4

            if ap >= 2 and met_haein:
                textbutton "💬  與車海仁交談　（消耗 2 AP）　好感度提升":
                    action Return("talk_haein")
                    text_color "#F8C8D0"
                    text_size 17
                    background "#200D18"
                    hover_background "#301525"
                    xsize 568
                    ypadding 10
            elif met_haein:
                text "💬  與車海仁交談　（AP 不足）" color "#404A50" size 17

            null height 4

            if ap >= 3:
                textbutton "🏚  進入地下城　（消耗 3 AP）　獲得 EXP":
                    action Return("dungeon")
                    text_color "#FFD080"
                    text_size 17
                    background "#1C1508"
                    hover_background "#2C2010"
                    xsize 568
                    ypadding 10
            else:
                text "🏚  進入地下城　（AP 不足）" color "#404A50" size 17

            null height 4

            if ap >= 1:
                textbutton "🛏  休息　　　　（消耗 1 AP）　HP / MP 完全恢復":
                    action Return("rest")
                    text_color "#90EE90"
                    text_size 17
                    background "#0C180C"
                    hover_background "#182818"
                    xsize 568
                    ypadding 10
            else:
                text "🛏  休息　　　　（AP 不足）" color "#404A50" size 17

            null height 10
            text "─────────────────────────────" color "#2A3A4A" size 15
            null height 6

            textbutton "💤  結束今天（進入下一日）":
                action Return("next_day")
                text_color "#909090"
                text_size 17
                background "#141414"
                hover_background "#222222"
                xsize 568
                ypadding 10
