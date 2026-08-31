#!/usr/bin/env python3
"""為指定影片產生 2x2 拼接縮圖（4 個時間點），供人眼判讀是否有拍到墨墨。"""
import os
import sys
import subprocess

SRC_DIR = "/Volumes/1TOWC/墨墨/攝影機錄影存檔"
OUT_DIR = "/tmp/momo_montages"
os.makedirs(OUT_DIR, exist_ok=True)


def get_duration(path: str) -> float:
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', path],
        capture_output=True, text=True, timeout=30
    ).stdout.strip()
    try:
        return float(out) if out else 0.0
    except ValueError:
        return 0.0


def make_montage(filename: str, subdir: str) -> str | None:
    path = os.path.join(SRC_DIR, subdir, filename)
    if not os.path.exists(path):
        return None
    duration = get_duration(path) or 60.0
    base = os.path.splitext(filename)[0]
    out_path = os.path.join(OUT_DIR, f"{base}.jpg")
    fractions = [0.15, 0.4, 0.6, 0.85]
    filter_parts = []
    inputs = []
    for i, frac in enumerate(fractions):
        ts = duration * frac
        inputs += ['-ss', str(ts), '-i', path]
    filter_complex = (
        "[0:v]scale=640:360[a];[1:v]scale=640:360[b];"
        "[2:v]scale=640:360[c];[3:v]scale=640:360[d];"
        "[a][b]hstack=inputs=2[top];[c][d]hstack=inputs=2[bot];"
        "[top][bot]vstack=inputs=2[out]"
    )
    cmd = ['ffmpeg', '-y'] + inputs + [
        '-filter_complex', filter_complex, '-map', '[out]',
        '-vframes', '1', '-q:v', '4', out_path
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return out_path if os.path.exists(out_path) else None


if __name__ == '__main__':
    subdir = sys.argv[1]
    filenames = sys.argv[2:]
    for fn in filenames:
        result = make_montage(fn, subdir)
        print(f"{fn} -> {result}")
