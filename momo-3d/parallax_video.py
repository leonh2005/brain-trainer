#!/usr/bin/env python3
"""墨墨偽 3D 視差影片（分層渲染，消除撕裂感）。

昨天的做法把整張圖逐像素位移，深度不連續處（主體邊緣 vs 背景）
兩側位移量差異大，中間就被拉扯出撕裂。
今天改成：背景抽成獨立靜止層並修補被主體遮住的區域，只有主體依深度
位移，再以羽化 alpha 合成——背景不再被拖動，撕裂即消失。

流程：照片 → 去背 → 深度 → 背景修補 → 分層視差 → 10 秒 mp4

用法：
    ./venv/bin/python parallax_video.py [來源檔] [輸出mp4]
"""
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import torch
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DEFAULT = os.path.join(BASE, "source/momo.jpg")
OUT_DEFAULT = os.path.join(BASE, "output/momo_parallax.mp4")

CROP = (700, 0, 2300)     # 昨天的裁切座標（左, 上, 邊長）
WORK = 960                # 工作／輸出解析度
FPS = 24
DURATION = 10.0           # 影片秒數
PERIOD = 5.0              # 一個完整來回秒數
AMPLITUDE = 26.0          # 最大水平位移（像素）
FEATHER = 0.010           # 主體邊緣羽化（相對 WORK）
BG_SHIFT = 0.16           # 背景視差比例（0 = 完全靜止）
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def segment(img: Image.Image) -> np.ndarray:
    """去背，回傳 0..1 alpha。"""
    from transformers import AutoModelForImageSegmentation
    from torchvision import transforms

    model = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet_lite", trust_remote_code=True).to(DEVICE).eval()
    tf = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    with torch.no_grad():
        pred = model(tf(img).unsqueeze(0).to(DEVICE))[-1].sigmoid().cpu()[0, 0].numpy()
    a = np.asarray(Image.fromarray((pred * 255).astype(np.uint8))
                   .resize(img.size, Image.LANCZOS)).astype(np.float32) / 255.0
    return np.clip((a - 0.35) / 0.3, 0, 1)


def estimate_depth(img: Image.Image) -> np.ndarray:
    """回傳 0..1 深度圖，1 = 近。"""
    from transformers import pipeline
    pipe = pipeline("depth-estimation",
                    model="depth-anything/Depth-Anything-V2-Base-hf", device=DEVICE)
    d = np.asarray(pipe(img)["depth"]).astype(np.float32)
    return (d - d.min()) / (d.max() - d.min() + 1e-6)


def subject_depth(depth: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """把深度重映射到主體自身的範圍，並平滑掉量化階梯。"""
    from scipy.ndimage import gaussian_filter
    fg = alpha > 0.5
    lo, hi = np.percentile(depth[fg], [2, 98]) if fg.any() else (0.0, 1.0)
    d = np.clip((depth - lo) / (hi - lo + 1e-6), 0, 1)
    return gaussian_filter(d, sigma=WORK * 0.004)


def inpaint_background(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """把主體挖掉後用最近鄰背景填補，供位移後露出的區域使用。"""
    from scipy.ndimage import distance_transform_edt, gaussian_filter
    hole = alpha > 0.03
    idx = distance_transform_edt(hole, return_distances=False, return_indices=True)
    filled = rgb[tuple(idx)]
    # 輕微柔化讓填補區不至於有硬邊
    return gaussian_filter(filled, sigma=(WORK * 0.006, WORK * 0.006, 0))


def warp(layer: np.ndarray, disp_x: np.ndarray) -> np.ndarray:
    """依水平位移場取樣。layer 可為 (h,w,3) 或 (h,w,4)。"""
    from scipy.ndimage import map_coordinates
    h, w = disp_x.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(xs - disp_x, 0, w - 1)
    out = np.empty_like(layer)
    for c in range(layer.shape[2]):
        out[:, :, c] = map_coordinates(layer[:, :, c], [ys, sx], order=1, mode="nearest")
    return out


def render_frame(bg: np.ndarray, fg: np.ndarray, disp: np.ndarray) -> Image.Image:
    """分層合成：靜止背景 + 位移後的主體。"""
    fg_w = warp(fg, disp)
    a = fg_w[:, :, 3:4] / 255.0
    comp = bg * (1 - a) + fg_w[:, :, :3] * a
    return Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8))


def contact_sheet(frames: list[Image.Image], labels: list[str], out_path: str) -> None:
    cell, pad, lab = 420, 10, 28
    sheet = Image.new("RGB", (len(frames) * (cell + pad) + pad, cell + lab + pad), (18, 18, 20))
    dr = ImageDraw.Draw(sheet)
    for i, (f, t) in enumerate(zip(frames, labels)):
        x = pad + i * (cell + pad)
        sheet.paste(f.resize((cell, cell), Image.LANCZOS), (x, lab))
        dr.text((x, 8), t, fill=(255, 214, 0))
    sheet.save(out_path)


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else SRC_DEFAULT
    out = sys.argv[2] if len(sys.argv) > 2 else OUT_DEFAULT
    workdir = os.path.join(BASE, "work/frames")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    left, top, side = CROP
    im = Image.open(src).convert("RGB")
    im = im.crop((left, top, left + side, top + side)).resize((WORK, WORK), Image.LANCZOS)
    rgb = np.asarray(im).astype(np.float32)
    print(f"來源 {os.path.basename(src)} 裁切 {side}px → {WORK}px  (device={DEVICE})")

    print("去背中…")
    alpha = segment(im)
    print(f"  前景占比 {(alpha > 0.5).mean() * 100:.1f}%")

    print("深度估計中…")
    depth = subject_depth(estimate_depth(im), alpha)

    print("背景修補中…")
    bg = inpaint_background(rgb, alpha)

    # 主體層：RGB + 羽化 alpha
    from scipy.ndimage import gaussian_filter
    a_soft = gaussian_filter(alpha, sigma=WORK * FEATHER)
    fg = np.dstack([rgb, a_soft * 255.0])

    total = int(FPS * DURATION)
    period_frames = FPS * PERIOD
    # 取樣四個相位（極值與零點各半），才能看出位移差異
    key_idx = {int(period_frames * f) for f in (0.0, 0.25, 0.5, 0.75)}
    print(f"渲染 {total} 格（{DURATION:.0f}s @ {FPS}fps，週期 {PERIOD:.0f}s）…")
    keys: dict[int, Image.Image] = {}

    def shift_at(i: int) -> float:
        return AMPLITUDE * math.sin(2 * math.pi * i / period_frames)

    for i in range(total):
        disp = shift_at(i) * (BG_SHIFT + (1 - BG_SHIFT) * depth)
        frame = render_frame(bg, fg, disp)
        frame.save(os.path.join(workdir, f"f_{i:04d}.png"))
        if i in key_idx:
            keys[i] = frame
        if i % 48 == 0:
            print(f"  {i}/{total}")

    print("合成影片…")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS), "-i", os.path.join(workdir, "f_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out,
    ], check=True)
    print(f"影片 → {out}  ({os.path.getsize(out) / 1024 / 1024:.1f} MB)")

    # 同步產一份 GIF（Telegram 會自動播放）
    gif_path = os.path.splitext(out)[0] + ".gif"
    step = 4                            # 抽 1/4 格數，控制 GIF 檔案大小
    idxs = list(range(total))[::step]
    frames = [Image.open(os.path.join(workdir, f"f_{i:04d}.png"))
              .resize((480, 480), Image.LANCZOS)
              .convert("P", palette=Image.ADAPTIVE, colors=128) for i in idxs]
    frames[0].save(gif_path, save_all=True, append_images=frames[1:],
                   duration=int(1000 * step / FPS), loop=0, optimize=True)
    print(f"GIF → {gif_path}  ({os.path.getsize(gif_path) / 1024:.0f} KB)")

    contact_sheet(
        [keys[k] for k in sorted(keys)],
        [f"frame {k} (shift {AMPLITUDE * math.sin(2 * math.pi * k / (FPS * PERIOD)):+.0f}px)"
         for k in sorted(keys)],
        os.path.join(BASE, "output/parallax_video_frames.png"),
    )


if __name__ == "__main__":
    main()
