#!/usr/bin/env python3
"""監視器影片片段的偽 3D 視差測試。

與照片版不同：影片每格都要重新去背與估深度，且逐格獨立的深度圖會
閃爍，所以對 alpha 與深度做時間軸 EMA 平滑。

用法：
    ./venv/bin/python clip_parallax.py <影片> <起始秒> <長度秒> [輸出mp4]
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
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

WORK = 960            # 工作解析度（長邊）
FPS = 30              # 沿用來源影格率
AMPLITUDE = 20.0      # 最大水平位移
PERIOD = 4.0          # 視差來回週期（秒）
FEATHER = 0.010
BG_SHIFT = 0.18
EMA = 0.45            # 時間軸平滑係數（越大越平滑、越延遲）

_seg_model = None
_depth_pipe = None


def seg_model():
    global _seg_model
    if _seg_model is None:
        from transformers import AutoModelForImageSegmentation
        _seg_model = AutoModelForImageSegmentation.from_pretrained(
            "ZhengPeng7/BiRefNet_lite", trust_remote_code=True).to(DEVICE).eval()
    return _seg_model


def depth_pipe():
    global _depth_pipe
    if _depth_pipe is None:
        from transformers import pipeline
        _depth_pipe = pipeline("depth-estimation",
                               model="depth-anything/Depth-Anything-V2-Base-hf",
                               device=DEVICE)
    return _depth_pipe


def segment(img: Image.Image) -> np.ndarray:
    from torchvision import transforms
    tf = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    with torch.no_grad():
        pred = seg_model()(tf(img).unsqueeze(0).to(DEVICE))[-1].sigmoid().cpu()[0, 0].numpy()
    a = np.asarray(Image.fromarray((pred * 255).astype(np.uint8))
                   .resize(img.size, Image.LANCZOS)).astype(np.float32) / 255.0
    return np.clip((a - 0.35) / 0.3, 0, 1)


def depth_of(img: Image.Image) -> np.ndarray:
    d = np.asarray(depth_pipe()(img)["depth"]).astype(np.float32)
    return (d - d.min()) / (d.max() - d.min() + 1e-6)


def warp(layer: np.ndarray, disp_x: np.ndarray) -> np.ndarray:
    from scipy.ndimage import map_coordinates
    h, w = disp_x.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(xs - disp_x, 0, w - 1)
    out = np.empty_like(layer)
    for c in range(layer.shape[2]):
        out[:, :, c] = map_coordinates(layer[:, :, c], [ys, sx], order=1, mode="nearest")
    return out


def extract(video: str, start: float, dur: float, outdir: str) -> list[str]:
    """用 trim 而非 -ss 抽格：-ss 對這批監視器檔案的定位會嚴重偏移。"""
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-i", video,
        "-vf", f"trim=start={start}:duration={dur},setpts=PTS-STARTPTS,scale={WORK}:-2",
        "-y", os.path.join(outdir, "%04d.png"),
    ], check=True)
    return sorted(os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".png"))


def main() -> None:
    video = sys.argv[1]
    start, dur = float(sys.argv[2]), float(sys.argv[3])
    out = sys.argv[4] if len(sys.argv) > 4 else os.path.join(BASE, "output/clip_parallax.mp4")
    framedir = os.path.join(BASE, "work/clip_src")
    renderdir = os.path.join(BASE, "work/clip_out")

    files = extract(video, start, dur, framedir)
    n = len(files)
    print(f"{os.path.basename(video)}  {start:.0f}s 起 {dur:.0f}s → {n} 格  (device={DEVICE})")
    if n == 0:
        raise SystemExit("沒有取到任何影格")

    from scipy.ndimage import gaussian_filter
    shutil.rmtree(renderdir, ignore_errors=True)
    os.makedirs(renderdir, exist_ok=True)

    a_ema = d_ema = None
    keys: dict[int, Image.Image] = {}
    key_idx = {int(FPS * PERIOD * f) for f in (0.0, 0.25, 0.5, 0.75)}

    for i, f in enumerate(files):
        im = Image.open(f).convert("RGB")
        rgb = np.asarray(im).astype(np.float32)

        a = segment(im)
        d = depth_of(im)
        # 時間軸平滑：逐格獨立估的結果會抖，混入前一格穩定下來
        a_ema = a if a_ema is None else EMA * a_ema + (1 - EMA) * a
        d_ema = d if d_ema is None else EMA * d_ema + (1 - EMA) * d

        fg_mask = a_ema > 0.5
        lo, hi = np.percentile(d_ema[fg_mask], [2, 98]) if fg_mask.any() else (0.0, 1.0)
        dn = gaussian_filter(np.clip((d_ema - lo) / (hi - lo + 1e-6), 0, 1), sigma=WORK * 0.004)

        # 背景修補：主體挖掉後用最近鄰填補
        from scipy.ndimage import distance_transform_edt, gaussian_filter as gf
        idx = distance_transform_edt(a_ema > 0.03, return_distances=False, return_indices=True)
        bg = gf(rgb[tuple(idx)], sigma=(WORK * 0.006, WORK * 0.006, 0))

        a_soft = gf(a_ema, sigma=WORK * FEATHER)
        fg = np.dstack([rgb, a_soft * 255.0])

        shift = AMPLITUDE * math.sin(2 * math.pi * i / (FPS * PERIOD))
        disp = shift * (BG_SHIFT + (1 - BG_SHIFT) * dn)

        fg_w = warp(fg, disp)
        al = fg_w[:, :, 3:4] / 255.0
        comp = np.clip(bg * (1 - al) + fg_w[:, :, :3] * al, 0, 255).astype(np.uint8)
        frame = Image.fromarray(comp)
        frame.save(os.path.join(renderdir, f"{i:04d}.png"))
        if i in key_idx:
            keys[i] = frame
        if i % 25 == 0:
            print(f"  {i}/{n}")

    print("合成中…")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-i", os.path.join(renderdir, "%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out,
    ], check=True)
    print(f"→ {out}  ({os.path.getsize(out) / 1024 / 1024:.1f} MB)")

    gif = os.path.splitext(out)[0] + ".gif"
    step = 2
    fr = [Image.open(os.path.join(renderdir, f"{i:04d}.png"))
          .resize((520, 520 * Image.open(os.path.join(renderdir, f'{i:04d}.png')).height
                   // Image.open(os.path.join(renderdir, f'{i:04d}.png')).width), Image.LANCZOS)
          .convert("P", palette=Image.ADAPTIVE, colors=128) for i in range(0, n, step)]
    fr[0].save(gif, save_all=True, append_images=fr[1:],
               duration=int(1000 * step / FPS), loop=0, optimize=True)
    print(f"GIF → {gif}  ({os.path.getsize(gif) / 1024:.0f} KB)")

    if keys:
        ordered = [keys[k] for k in sorted(keys)]
        sheet = Image.new("RGB", (len(ordered) * 430 + 10, 470), (18, 18, 20))
        dr = ImageDraw.Draw(sheet)
        for j, (k, fr_) in enumerate(zip(sorted(keys), ordered)):
            sheet.paste(fr_.resize((420, 420 * fr_.height // fr_.width), Image.LANCZOS),
                        (10 + j * 430, 30))
            dr.text((10 + j * 430, 8),
                    f"frame {k} shift {AMPLITUDE * math.sin(2 * math.pi * k / (FPS * PERIOD)):+.0f}px",
                    fill=(255, 214, 0))
        sheet.save(os.path.join(BASE, "output/clip_parallax_frames.png"))


if __name__ == "__main__":
    main()
