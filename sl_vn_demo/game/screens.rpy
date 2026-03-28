## 獨自覺醒 — 標準 UI 畫面（賽博龐克風格）

################################################################################
## Say 對話畫面
################################################################################

screen say(who, what):
    style_prefix "say"

    window:
        id "window"

        if who is not None:
            window:
                style "namebox"
                text who id "who"

        text what id "what"

style window:
    xalign 0.5
    xfill True
    yalign 1.0
    ysize 185
    background Frame(Solid("#0A0E1EDD"), 20, 20)
    padding (40, 20)

style namebox:
    xpos 100
    xanchor 0.0
    ypos -42
    ysize 36
    background Frame(Solid("#0D2A40EE"), 10, 5)
    padding (16, 6)

style say_label:
    color "#4FC3F7"
    size 22
    bold True

style say_dialogue:
    color "#E8F4FF"
    size 22
    xpos 40
    xsize 1200
    ypos 18
    line_spacing 6


################################################################################
## Input 輸入畫面
################################################################################

screen input(prompt):
    style_prefix "input"

    window:
        vbox:
            xalign 0.5
            xpos 0
            xsize 1280
            ypos 600

            text prompt style "input_prompt"
            input id "input"

style input_prompt:
    xalign 0.5
    color "#4FC3F7"
    size 22

style input:
    xalign 0.5
    color "#FFFFFF"
    size 24
    xmaximum 600


################################################################################
## Choice 選項畫面
################################################################################

screen choice(items):
    style_prefix "choice"

    vbox:
        for i in items:
            textbutton i.caption action i.action

style choice_vbox:
    xalign 0.5
    yalign 0.5
    spacing 14

style choice_button:
    xsize 700
    ypadding 14
    xpadding 30
    background "#0D2030DD"
    hover_background "#1A3A50EE"

style choice_button_text:
    color "#D8EEFF"
    size 22
    xalign 0.5
    hover_color "#4FC3F7"


################################################################################
## Quick Menu 快速選單（遊戲中底部）
################################################################################

screen quick_menu():
    zorder 100

    if quick_menu:
        hbox:
            style_prefix "quick"
            xalign 0.5
            yalign 1.0
            yoffset -10

            textbutton "返回" action Rollback()
            textbutton "歷史" action ShowMenu('history')
            textbutton "略過" action Skip() alternate Skip(fast=True, confirm=True)
            textbutton "自動" action Preference("auto-forward", "toggle")
            textbutton "存檔" action ShowMenu('save')
            textbutton "讀檔" action ShowMenu('load')
            textbutton "設定" action ShowMenu('preferences')

init python:
    config.overlay_screens.append("quick_menu")

default quick_menu = True

style quick_button:
    background "#0A0E1ECC"
    hover_background "#1A2A3ECC"
    xpadding 14
    ypadding 8

style quick_button_text:
    color "#7090A8"
    size 16
    hover_color "#4FC3F7"


################################################################################
## 遊戲選單框架
################################################################################

screen game_menu(title, scroll=None):
    style_prefix "game_menu"
    modal True

    zorder 200

    add Solid("#05080FEE")

    frame:
        style "game_menu_outer_frame"

        hbox:
            frame:
                style "game_menu_navigation_frame"

                vbox:
                    style_prefix "navigation"
                    spacing 8
                    xalign 0.5
                    yalign 0.3

                    if not main_menu:
                        textbutton "歷史" action ShowMenu("history")
                        textbutton "存檔" action ShowMenu("save")

                    textbutton "讀檔" action ShowMenu("load")
                    textbutton "設定" action ShowMenu("preferences")

                    if _in_replay:
                        textbutton "結束回放" action EndReplay(confirm=True)
                    elif not main_menu:
                        textbutton "主選單" action MainMenu()

                    textbutton "關於" action ShowMenu("about")
                    textbutton "離開" action Quit(confirm=not main_menu)

            frame:
                style "game_menu_content_frame"

                if scroll == "viewport":
                    viewport:
                        scrollbars "vertical"
                        mousewheel True
                        transclude

                elif scroll == "vpgrid":
                    vpgrid:
                        cols 1
                        yinitial 1.0
                        scrollbars "vertical"
                        mousewheel True
                        transclude

                else:
                    transclude

    text title:
        style "game_menu_title"

    textbutton "返回":
        style "return_button"
        action Return()

style game_menu_outer_frame:
    bottom_margin 30
    top_margin 120
    background None

style game_menu_navigation_frame:
    xsize 240
    yfill True
    background "#08101ADD"

style game_menu_content_frame:
    left_margin 50
    right_margin 30
    top_margin 10

style game_menu_title:
    xpos 60
    ysize 100
    color "#4FC3F7"
    size 34
    bold True
    yalign 0.5

style return_button:
    xpos 60
    xanchor 0.0
    xsize 200
    yalign 1.0
    yoffset -30
    background "#0D2030"
    hover_background "#1A3A50"
    xpadding 20
    ypadding 10

style return_button_text:
    color "#AACCDD"
    size 18
    hover_color "#4FC3F7"

style navigation_button:
    xsize 200
    xpadding 20
    ypadding 10
    background None
    hover_background "#1A2A3AEE"

style navigation_button_text:
    color "#90AAC0"
    size 20
    hover_color "#4FC3F7"


################################################################################
## 存檔畫面
################################################################################

screen save():
    tag menu
    use game_menu("存　檔", scroll="vpgrid"):
        use file_slots("存檔")

screen load():
    tag menu
    use game_menu("讀　檔", scroll="vpgrid"):
        use file_slots("讀檔")

screen file_slots(title):
    default page_name_value = FilePageNameInputValue(pattern="{}", auto="自動", quick="快速")

    vbox:
        spacing 15

        hbox:
            style_prefix "page"
            xalign 0.5
            spacing 20

            textbutton "<" action FilePagePrevious()

            if config.has_autosave:
                textbutton _("{#auto_page}自動") action FilePage("auto")

            if config.has_quicksave:
                textbutton _("{#quick_page}快速") action FilePage("quick")

            for page in range(1, 11):
                textbutton str(page) action FilePage(page)

            textbutton ">" action FilePageNext()

        vpgrid:
            cols 2
            yinitial 1.0
            spacing 15

            for i in range(1, 9):
                button:
                    style "slot_button"
                    action FileAction(i)

                    has vbox

                    add FileScreenshot(i) xsize 256 ysize 144

                    text FileTime(i, format=_("{#file_time}%A, %B %d %Y, %H:%M"), empty=_("空槽")):
                        style "slot_time"

                    text FileSaveName(i):
                        style "slot_name"

                    key "save_delete" action FileDelete(i)

style page_button:
    xsize 50
    ypadding 6
    xpadding 10
    background "#0D2030"
    hover_background "#1A3A50"

style page_button_text:
    color "#7090A8"
    size 18
    hover_color "#4FC3F7"

style slot_button:
    background "#09131ECC"
    hover_background "#0F1F2ECC"
    xpadding 10
    ypadding 10
    xsize 380

style slot_time:
    color "#607080"
    size 15

style slot_name:
    color "#90A8B8"
    size 17


################################################################################
## 設定畫面
################################################################################

screen preferences():
    tag menu
    use game_menu("設　定", scroll="viewport"):
        vbox:
            spacing 30

            hbox:
                box_wrap True
                spacing 50

                vbox:
                    style_prefix "pref"
                    label "顯示模式"
                    if renpy.variant("pc"):
                        textbutton "視窗"   action Preference("display", "window")
                        textbutton "全螢幕" action Preference("display", "fullscreen")

                vbox:
                    style_prefix "pref"
                    label "跳過模式"
                    textbutton "略過模式" action Preference("skip", "toggle")
                    textbutton "自動前進" action Preference("auto-forward", "toggle")

            if config.has_music:
                vbox:
                    style_prefix "slider"
                    box_wrap True

                    label "音樂音量"

                    hbox:
                        bar value Preference("music volume")

            if config.has_sound:
                vbox:
                    style_prefix "slider"
                    box_wrap True

                    label "音效音量"

                    hbox:
                        bar value Preference("sound volume")

                        if config.sample_sound:
                            textbutton _("測試") action Play("sound", config.sample_sound)

            vbox:
                style_prefix "slider"
                box_wrap True

                label "文字速度"
                hbox:
                    bar value Preference("text speed")

            vbox:
                style_prefix "slider"
                box_wrap True

                label "自動前進速度"
                hbox:
                    bar value Preference("auto-forward time")

style pref_label:
    top_margin 10
    bottom_margin 6

style pref_label_text:
    color "#4FC3F7"
    size 20
    bold True

style pref_button:
    xsize 200
    ypadding 8
    xpadding 16
    background "#0D2030"
    hover_background "#1A3A50"

style pref_button_text:
    color "#90AACC"
    size 18
    hover_color "#4FC3F7"

style slider_label:
    top_margin 10
    bottom_margin 6

style slider_label_text:
    color "#4FC3F7"
    size 20
    bold True

style slider_hbox:
    spacing 15

style slider_button:
    xsize 110
    ypadding 6
    xpadding 12
    background "#0D2030"
    hover_background "#1A3A50"

style slider_button_text:
    color "#90AACC"
    size 16

style slider_bar:
    xsize 380
    ysize 24
    base_bar Frame(Solid("#1A2A3A"), 10, 5)
    thumb Solid("#4FC3F7", xsize=12, ysize=24)


################################################################################
## 歷史畫面
################################################################################

screen history():
    tag menu
    predict False
    use game_menu("對話歷史", scroll="vpgrid"):
        style_prefix "history"
        for h in renpy.history():
            window:
                has fixed:
                    yfit True

                if h.who:
                    label h.who:
                        style "history_name"
                        xpos 0
                        xsize 200

                $ what = renpy.filter_text_tags(h.what, allow=gui.history_allow_tags if hasattr(gui, 'history_allow_tags') else [])
                text what:
                    xpos 220
                    xsize 860
                    ypos 0

style history_window:
    padding (10, 6)
    background "#08101A88"

style history_name:
    color "#4FC3F7"
    size 20

style history_text:
    color "#C8D8E8"
    size 20


################################################################################
## 關於畫面
################################################################################

screen about():
    tag menu
    use game_menu("關　於"):
        style_prefix "about"
        vbox:
            spacing 16
            label "獨自覺醒":
                text_color "#4FC3F7"
                text_size 28
                text_bold True

            text "Demo v0.1":
                color "#76FF03"
                size 18

            null height 16
            text "故事：Steven":
                color "#90A8B8"
                size 18
            text "程式：Claude (Anthropic)":
                color "#90A8B8"
                size 18

style about_vbox:
    xalign 0.5
    yalign 0.3


################################################################################
## Notify 通知
################################################################################

screen notify(message):
    zorder 100
    style_prefix "notify"

    frame at notify_appear:
        text "[message!tq]"

    timer 3.25 action Hide('notify')

transform notify_appear:
    on show:
        alpha 0
        linear .25 alpha 1.0
    on hide:
        linear .5 alpha 0.0

style notify_frame:
    xpos 60
    xanchor 0.0
    ypos 70
    background "#0A1624EE"
    padding (20, 10)

style notify_text:
    color "#76FF03"
    size 18


################################################################################
## Skip 跳過指示
################################################################################

screen skip_indicator():
    zorder 100
    style_prefix "skip"

    frame:
        hbox:
            spacing 6
            text _("跳過")
            text "▸▸"

style skip_frame:
    ypos 10
    background "#09131EEE"
    padding (12, 6)

style skip_text:
    color "#607080"
    size 16


################################################################################
## NVL 模式
################################################################################

screen nvl(dialogue, items=None):
    style_prefix "nvl"
    window:
        has vbox:
            spacing 12

            for d in dialogue:
                window:
                    id d.window_id

                    has hbox:
                        spacing 20

                        if d.who is not None:
                            text d.who:
                                style "nvl_name"
                                xsize 150

                        text d.what:
                            style "nvl_dialogue"
                            xsize 870

        if items is not None:
            for i in items:
                textbutton i.caption:
                    action i.action
                    style "nvl_button"

style nvl_window:
    xfill True
    yfill True
    background "#000000CC"
    padding (30, 30)

style nvl_name:
    color "#4FC3F7"
    size 20
    bold True

style nvl_dialogue:
    color "#D8E8F0"
    size 20

style nvl_button:
    xalign 0.5
    xsize 600
    ypadding 10
    xpadding 20
    background "#0D2030"
    hover_background "#1A3A50"

style nvl_button_text:
    color "#D8EEFF"
    size 20
    xalign 0.5
    hover_color "#4FC3F7"
