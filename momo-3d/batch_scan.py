#!/usr/bin/env python3
"""批次掃描 MOMORECORD 的監視器錄影，挑出可用的墨墨片段。

篩選條件依 Steven 提供的作息（早上 7 點到中午 12 點活動機率高）：
  1. 檔名起始時間落在 07:00-12:00
  2. 該時段是彩色（紅外線黑白做視差效果差）
  3. 曝光穩定（避開日夜模式切換的假動作）
  4. 畫面內有明顯動作

每支影片存下動作最強的前幾個時段與對應縮圖，供人工挑選。

用法：
    ./venv/bin/python batch_scan.py [--limit N] [--top-per-video 3]
"""
import argparse
import glob
import json
import os
import subprocess

import numpy as np
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = "/Volumes/MOMORECORD"
OUT_DIR = os.path.join(BASE, "output/candidates")

W, H, FPS = 320, 180, 1
SAT_MIN = 18.0        # 飽和度門檻：低於此視為紅外線黑白
JUMP_MAX = 20.0       # 亮度瞬變門檻：超過視為模式切換


def load_frames(path: str) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    return np.frombuffer(raw[: n * W * H * 3], dtype=np.uint8).reshape(n, H, W, 3)


def metrics(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = frames.astype(np.float32)
    sat = (f.max(axis=3) - f.min(axis=3)).mean(axis=(1, 2))
    bri = f.mean(axis=(1, 2, 3))
    diff = np.concatenate([[0.0], np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3))])
    jump = np.concatenate([[0.0], np.abs(np.diff(bri))])
    motion = np.where(jump > JUMP_MAX, 0.0, diff)
    return sat, bri, motion


def pick_moments(sat: np.ndarray, motion: np.ndarray, top: int) -> list[int]:
    """在彩色且曝穩的時段裡挑動作最強的幾個時間點，彼此至少隔 20 秒。"""
    score = np.where(sat > SAT_MIN, motion, 0.0)
    picked: list[int] = []
    for i in np.argsort(score)[::-1]:
        if score[i] <= 0 or len(picked) >= top:
            break
        if all(abs(int(i) - p) > 20 for p in picked):
            picked.append(int(i))
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 支（0 = 全部）")
    ap.add_argument("--top-per-video", type=int, default=3)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SRC_DIR, "*.mp4")))
    files = [f for f in files if "070000" <= os.path.basename(f).split("_")[1] <= "120000"]
    if args.limit:
        files = files[: args.limit]
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"待掃描 {len(files)} 支（來源時間 07:00-12:00）", flush=True)

    results = []
    for vi, path in enumerate(files, 1):
        name = os.path.basename(path)
        try:
            frames = load_frames(path)
        except subprocess.CalledProcessError:
            print(f"[{vi}/{len(files)}] {name} 解碼失敗，跳過", flush=True)
            continue
        sat, bri, motion = metrics(frames)
        usec = sat > SAT_MIN
        color_ratio = float(usec.mean())

        moments = pick_moments(sat, motion, args.top_per_video)
        entry = {"file": name, "minutes": round(len(frames) / 60, 1),
                 "color_ratio": round(color_ratio, 3),
                 "moments": []}
        base = motion[usec].mean() if usec.any() else 1.0

        for rank, sec in enumerate(moments):
            mm, ss = divmod(sec, 60)
            strength = float(motion[max(0, sec - 2): sec + 3].mean() / max(base, 1e-6))
            thumb = os.path.join(OUT_DIR, f"{vi:03d}_{mm:02d}m{ss:02d}s_{strength:.1f}x.png")
            Image.fromarray(frames[sec]).save(thumb)
            entry["moments"].append({"sec": sec, "label": f"{mm:02d}:{ss:02d}",
                                     "strength": round(strength, 1), "thumb": os.path.basename(thumb)})

        results.append(entry)
        best = f"{entry['moments'][0]['label']} ({entry['moments'][0]['strength']}x)" if moments else "無"
        print(f"[{vi}/{len(files)}] {name}  {entry['minutes']}分  彩色{color_ratio*100:.0f}%  最佳 {best}",
              flush=True)

    out_json = os.path.join(BASE, "output/scan_results.json")
    with open(out_json, "w") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n結果 → {out_json}   縮圖 → {OUT_DIR}/", flush=True)


if __name__ == "__main__":
    main()
