#!/usr/bin/env python3
"""對候選時段抽連續畫面，確認「動的主體」是不是墨墨。

單格縮圖無法區分「墨墨在動」與「飼主的手在動、墨墨躺著」——兩者的
靜態畫面可能很像。所以要連續看 4 秒。

用法：
    ./venv/bin/python check_motion.py "<檔名>:<MM:SS>" ...
"""
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = "/Volumes/MOMORECORD"
SPAN, FPS = 4, 2


def to_sec(label: str) -> int:
    mm, ss = label.split(":")
    return int(mm) * 60 + int(ss)


def grab(video: str, sec: int, outdir: str) -> list[str]:
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-i", video,
        "-vf", f"trim=start={max(0, sec-1)}:duration={SPAN},setpts=PTS-STARTPTS,"
               f"fps={FPS},scale=380:-2",
        "-y", os.path.join(outdir, "%02d.png"),
    ], check=False)
    return sorted(os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".png"))


def main() -> None:
    specs = sys.argv[1:]
    if not specs:
        raise SystemExit("用法: check_motion.py <檔名>:<MM:SS> ...")

    rows = []
    for spec in specs:
        # 格式 <檔名>:<MM:SS>，檔名不含冒號，時間是最後兩段
        parts = spec.split(":")
        fname = parts[0]
        label = ":".join(parts[-2:])
        sec = to_sec(label)
        fs = grab(os.path.join(SRC_DIR, fname), sec, os.path.join(BASE, "work/chk", f"{fname}_{sec}"))
        print(f"{fname} {label} → {len(fs)} 格", flush=True)
        rows.append((fname, label, fs))

    cell, pad, lab = 380, 4, 22
    per_row = max(len(r[2]) for r in rows)
    W = per_row * (cell + pad) + pad
    H = len(rows) * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (16, 16, 18))
    dr = ImageDraw.Draw(sheet)
    for ri, (fname, label, fs) in enumerate(rows):
        y = pad + ri * (cell + lab + pad)
        dr.text((pad, y + 4), f"{fname}  {label}", fill=(0, 255, 140))
        for ci, f in enumerate(fs):
            im = Image.open(f).convert("RGB")
            x = pad + ci * (cell + pad)
            sheet.paste(im, (x, y + lab))
    out = os.path.join(BASE, "output/motion_check.png")
    sheet.save(out)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
