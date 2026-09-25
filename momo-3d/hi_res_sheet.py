#!/usr/bin/env python3
"""從原始監視器影片重抽候選片段的畫面，做成高解析度總覽。

掃描階段的縮圖只有 320x180，放大後看不清構圖，所以針對入選的候選
回原始影片（2560x1440）重抽。用 trim 而非 -ss 定位（-ss 對這批檔案
會嚴重偏移），一支影片的多個候選共用同一次解碼。

用法：
    ./venv/bin/python hi_res_sheet.py [--count 12]
"""
import argparse
import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = "/Volumes/MOMORECORD"
TMP = os.path.join(BASE, "work/hires")
W = 960


def label_to_sec(label: str) -> int:
    mm, ss = label.split(":")
    return int(mm) * 60 + int(ss)


def grab(video: str, sec: int, outdir: str) -> str | None:
    """抽出該時間點附近的一格，回傳檔案路徑。"""
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-i", video,
        "-vf", f"trim=start={sec}:duration=0.15,setpts=PTS-STARTPTS,scale={W}:-2",
        "-y", os.path.join(outdir, "%02d.png"),
    ], check=False)
    got = sorted(f for f in os.listdir(outdir) if f.endswith(".png"))
    return os.path.join(outdir, got[0]) if got else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=12)
    args = ap.parse_args()

    rows = json.load(open(os.path.join(BASE, "output/candidates_refined.json")))[: args.count]
    os.makedirs(TMP, exist_ok=True)

    picks = []
    by_file: dict[str, list] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)

    for fname, items in by_file.items():
        video = os.path.join(SRC_DIR, fname)
        for it in items:
            sec = label_to_sec(it["label"])
            p = grab(video, sec, os.path.join(TMP, f"{fname}_{sec}"))
            if p:
                picks.append((it, p))
                print(f"  ok  {fname} {it['label']}", flush=True)
            else:
                print(f"  失敗 {fname} {it['label']}（可能超出片長）", flush=True)

    if not picks:
        raise SystemExit("沒有取到任何畫面")

    CW, CH, PAD, LAB = 760, 428, 10, 34
    cols = 3
    rows_n = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (CW + PAD) + PAD, rows_n * (CH + LAB + PAD) + PAD), (16, 16, 18))
    dr = ImageDraw.Draw(sheet)
    for i, (it, p) in enumerate(picks):
        im = Image.open(p).convert("RGB")
        im = im.resize((CW, int(CW * im.height / im.width)), Image.LANCZOS)
        x = PAD + (i % cols) * (CW + PAD)
        y = PAD + (i // cols) * (CH + LAB + PAD)
        sheet.paste(im, (x, y + LAB))
        dr.text((x, y + 5), f'#{i+1:02d}  {it["strength"]:4.1f}x  {it["file"]}  {it["label"]}',
                fill=(255, 214, 0))
    out = os.path.join(BASE, "output/candidates_hires.png")
    sheet.save(out)
    print(f"\n→ {out}   ({len(picks)} 格)")

    desktop = os.path.expanduser("~/Desktop/墨墨片段候選_高解析.png")
    sheet.save(desktop)
    print(f"→ {desktop}")


if __name__ == "__main__":
    main()
