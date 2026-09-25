#!/usr/bin/env python3
"""找出墨墨在畫面中「連續停留」的區間。

原本只按動作量挑片段，結果選中的多半是墨墨快速經過鏡頭前的幾秒——
牠一走開畫面就空了。做 10 秒影片需要牠在同一個位置待得夠久。

這裡對候選時間點前後各掃一段，逐格去背算出主體佔比，回報連續在畫面中
的區間長度，並要求佔比落在指定範圍（距離適中）。

用法：
    ./venv/bin/python scan_presence.py "<檔名>:<MM:SS>" ... [--span 40] [--min-sec 8]
"""
import argparse
import os

import numpy as np
import torch
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = "/Volumes/MOMORECORD"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
FPS = 1
W = 320


def to_sec(label: str) -> int:
    mm, ss = label.split(":")
    return int(mm) * 60 + int(ss)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--span", type=int, default=40, help="中心點前後各掃幾秒")
    ap.add_argument("--min-sec", type=int, default=8, help="視為可用的最短連續秒數")
    ap.add_argument("--cover-min", type=float, default=0.08)
    ap.add_argument("--cover-max", type=float, default=0.55)
    ap.add_argument("--fps", type=float, default=FPS, help="抽樣頻率，掃長片時可調低")
    ap.add_argument("--full", action="store_true", help="掃整支影片（忽略中心點）")
    args = ap.parse_args()

    from transformers import AutoModelForImageSegmentation
    from torchvision import transforms
    model = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet_lite", trust_remote_code=True).to(DEVICE).eval()
    tf = transforms.Compose([
        transforms.Resize((512, 512)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    import subprocess, tempfile, shutil
    for spec in args.specs:
        parts = spec.split(":")
        fname, label = parts[0], ":".join(parts[-2:])
        center = to_sec(label)
        if args.full:
            start, dur = 0, 0          # 0 = 不裁，整支
        else:
            start, dur = max(0, center - args.span), args.span * 2
        trim = f"trim=start={start}:duration={dur}," if dur else ""
        tmp = tempfile.mkdtemp()
        subprocess.run([
            "ffmpeg", "-v", "error", "-i", os.path.join(SRC_DIR, fname),
            "-vf", f"{trim}setpts=PTS-STARTPTS,fps={args.fps},scale={W}:-2",
            "-y", os.path.join(tmp, "%04d.png"),
        ], check=False)
        files = sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.endswith(".png"))

        covers = []
        for i, p in enumerate(files):
            im = Image.open(p).convert("RGB")
            with torch.no_grad():
                pred = model(tf(im).unsqueeze(0).to(DEVICE))[-1].sigmoid().cpu()[0, 0].numpy()
            covers.append(float((pred > 0.5).mean()))
            if i % 20 == 0:
                print(f"  {fname} {i}/{len(files)}", flush=True)
        shutil.rmtree(tmp, ignore_errors=True)

        # 找出連續在畫面中的區間
        present = [args.cover_min <= c <= args.cover_max for c in covers]
        runs, cur = [], None
        for i, ok in enumerate(present):
            if ok and cur is None:
                cur = i
            elif not ok and cur is not None:
                runs.append((cur, i))
                cur = None
        if cur is not None:
            runs.append((cur, len(present)))

        usable = [(a, b) for a, b in runs if b - a >= args.min_sec]
        print(f"\n{fname} @ {label}（掃 {start}-{start+len(covers)}s）")
        print(f"  墨墨在畫面中的區間：")
        for a, b in runs:
            seg = covers[a:b]
            mark = "  ← 可用" if b - a >= args.min_sec else ""
            print(f"    {start+a}s - {start+b}s  {b-a:3d} 秒  "
                  f"佔比 {np.mean(seg)*100:.0f}%{mark}")
        if not usable:
            print("  ✗ 沒有夠長的連續區間")


if __name__ == "__main__":
    main()
