## 獨自覺醒 Demo — 遊戲設定

define config.name = "獨自覺醒"
define gui.show_name = True
define config.version = "Demo 0.1"
define gui.about = _p("")
define build.name = "sl_vn_demo"

define config.window_title = "獨自覺醒 — Demo"

define config.has_sound = True
define config.has_music = True
define config.has_voice = False

define config.enter_transition = dissolve
define config.exit_transition = dissolve
define config.intra_transition = dissolve
define config.after_load_transition = None
define config.end_game_transition = None
define config.window = "auto"
define config.window_show_transition = Dissolve(.2)
define config.window_hide_transition = Dissolve(.2)

default preferences.text_cps = 0
default preferences.afm_time = 15

define config.save_directory = "sl-vn-demo-saves"

define config.window_icon = None

## 中文字型（將 NotoSansTC-Regular.otf 放入 game/fonts/ 後取消註解）
# define gui.text_font = "fonts/NotoSansTC-Regular.otf"
# define gui.name_text_font = "fonts/NotoSansTC-Regular.otf"
# define gui.interface_text_font = "fonts/NotoSansTC-Regular.otf"

init python:
    build.classify('**~', None)
    build.classify('**.bak', None)
    build.classify('**/.**', None)
    build.classify('**/#**', None)
    build.classify('**/thumbs.db', None)
    build.documentation('*.html')
    build.documentation('*.txt')

init -999 python in gui:
    show_name = True

init 1000 python:
    import renpy
    print("=== TRANSLATE FILES ===")
    for f in renpy.config.translate_files:
        print(f)
    print("=== SEARCHPATH ===")
    for p in renpy.config.searchpath:
        print(p)
