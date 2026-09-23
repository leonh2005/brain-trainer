#!/usr/bin/env python3
"""用真深度圖做偽 3D 視差動畫（取代啟發式遮罩，消除邊緣鬼影）。

流程：照片 → 深度估計（Depth Anything V2）→ 依深度分層位移 → 240x240 GIF

用法：
    ./venv/bin/python depth_parallax.py [來源檔] [輸出gif]
"""
import os
import sys
import math
import numpy as np
import torch
from PIL import Image

SRC_DEFAULT = "/Users/steven/我的雲端硬碟/📱 媒體/手機影音/墨墨/DSC_0261.JPG"
OUT_DEFAULT = os.path.expanduser("~/CCProject/cube-portrait-test/output/depth_parallax_240.gif")

# 裁切參數（依先前座標定位校正）
CROP = (700, 0, 2300)      # left, top, side
WORK = 320                 # 工作解析度（留位移邊界）
OUT_SIZE = 240
N_FRAMES = 12              # 12 格 @ 12fps ≈ 1 秒循環
AMPLITUDE = 14             # 最大位移像素（工作解析度下，換算到 240px 約 ±10px）

device = "mps" if torch.backends.mps.is_available() else "cpu"


def estimate_depth(img: Image.Image) -> np.ndarray:
    """回傳 0..1 的深度圖，1 = 近。"""
    from transformers import pipeline
    pipe = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf", device=device)
    result = pipe(img)
    d = np.asarray(result["depth"]).astype(np.float32)
    d = (d - d.min()) / (d.max() - d.min() + 1e-6)
    return d  # Depth Anything: 亮 = 近


def render(full: np.ndarray, depth: np.ndarray, shift: float, size: int) -> Image.Image:
    """逐像素依深度水平位移：每個像素移多少由它自己的深度決定。

    這才是真正的視差渲染——「兩層混合」會在前後景交界產生鬼影，
    因為它假設畫面只有遠近兩層，而真實深度是連續的。
    """
    from scipy.ndimage import map_coordinates
    h, w, _ = full.shape
    ys, xs = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    # 近（depth→1）移得多，遠（depth→0）幾乎不動
    src_x = np.clip(xs - shift * depth, 0, w - 1)
    out = np.empty_like(full)
    for c in range(3):
        out[:, :, c] = map_coordinates(full[:, :, c], [ys, src_x], order=1, mode="nearest")
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return img.resize((size, size), Image.LANCZOS)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SRC_DEFAULT
    out_path = sys.argv[2] if len(sys.argv) > 2 else OUT_DEFAULT

    im = Image.open(src).convert("RGB")
    left, top, side = CROP
    crop = im.crop((left, top, left + side, top + side)).resize((WORK, WORK), Image.LANCZOS)
    print(f"來源 {os.path.basename(src)}  裁切 {side}px → 工作 {WORK}px")

    print(f"深度估計中（device={device}）…")
    depth = estimate_depth(crop)
    print(f"  深度範圍 {depth.min():.2f}–{depth.max():.2f}，平均 {depth.mean():.2f}")

    # 深度圖存檔供檢視
    outdir = os.path.dirname(out_path)
    Image.fromarray((depth * 255).astype(np.uint8)).resize((240, 240)).save(
        os.path.join(outdir, "depth_map.png"))

    full = np.asarray(crop).astype(np.float32)
    frames = []
    for i in range(N_FRAMES):
        shift = AMPLITUDE * math.sin(2 * math.pi * i / N_FRAMES)
        f = render(full, depth, shift, OUT_SIZE)
        frames.append(f)

    # 存成 GIF（量化到 256 色）
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in frames]
    pal[0].save(out_path, save_all=True, append_images=pal[1:], duration=83, loop=0, optimize=True)
    print(f"GIF → {out_path}  ({os.path.getsize(out_path)/1024:.0f} KB, {N_FRAMES} 格 12fps)")

    # 關鍵格對照
    sel = [frames[0], frames[N_FRAMES // 4], frames[N_FRAMES // 2], frames[3 * N_FRAMES // 4]]
    from PIL import ImageDraw
    sheet = Image.new("RGB", (4 * 500, 540), (18, 18, 18))
    dr = ImageDraw.Draw(sheet)
    for i, fr in enumerate(sel):
        sheet.paste(fr.resize((480, 480), Image.NEAREST), (i * 500 + 10, 40))
        dr.text((i * 500 + 10, 14), f"frame {[0, N_FRAMES//4, N_FRAMES//2, 3*N_FRAMES//4][i]}", fill=(255, 220, 0))
    sheet_path = os.path.join(outdir, "depth_parallax_frames.png")
    sheet.save(sheet_path)
    print(f"對照 → {sheet_path}")


if __name__ == "__main__":
    main()
