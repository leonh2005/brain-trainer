from PIL import Image
import os

SRC = "/Users/steven/Downloads/sddefault.jpg"
OUT = os.path.join(os.path.dirname(__file__), "../static/img/character_upper.png")

img = Image.open(SRC)
w, h = img.size  # 640x480

# 右側人物：從約 x=370 到 x=530，y=30 到 y=310（頭頂到腰部）
crop = img.crop((370, 30, 530, 310))
crop.save(OUT)
print(f"Saved {crop.size} → {OUT}")
