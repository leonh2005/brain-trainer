## 獨自覺醒 GUI 設定 — 含中文字型

init -2 python:
    gui.text_font            = "fonts/STHeiti.ttc"
    gui.name_text_font       = "fonts/STHeiti.ttc"
    gui.interface_text_font  = "fonts/STHeiti.ttc"
    gui.button_text_font     = "fonts/STHeiti.ttc"
    gui.choice_button_text_font = "fonts/STHeiti.ttc"

## 基本尺寸
init python:
    gui.init(1280, 720)

## 字體大小
define gui.text_size        = 22
define gui.name_text_size   = 22
define gui.interface_text_size = 20
