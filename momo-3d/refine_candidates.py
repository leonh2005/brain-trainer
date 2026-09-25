#!/usr/bin/env python3
"""重新排名候選片段：排除「人的手」造成的假動作。

初版掃描只量畫面變化，結果清籠、餵食、摸墨墨的人手動作全排在最前面，
墨墨自己的動作反被埋沒。人手是暖膚色、墨墨是灰色，所以用膚色比例過濾。

縮圖本身就是 320x180 的原始影格，因此不需重跑影片即可重新評分。

用法：
    ./venv/bin/python refine_candidates.py [--top 36]
"""
import argparse
import json
import os

import numpy as np
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
CAND_DIR = os.path.join(BASE, "output/candidates")
SKIN_MAX = 0.06        # 膚色占比上限，超過視為人手主導


def skin_ratio(path: str) -> float:
    """經典 RGB 膚色規則，回傳佔畫面比例。"""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = ((r > 95) & (g > 40) & (b > 20) &
            ((r - b) > 15) & (np.abs(r - g) > 15) & (r > g) & (r > b))
    return float(mask.mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=36)
    args = ap.parse_args()

    results = json.load(open(os.path.join(BASE, "output/scan_results.json")))
    rows = []
    for entry in results:
        for m in entry["moments"]:
            p = os.path.join(CAND_DIR, m["thumb"])
            if not os.path.exists(p):
                continue
            sr = skin_ratio(p)
            rows.append({"file": entry["file"], "label": m["label"],
                         "strength": m["strength"], "thumb": m["thumb"],
                         "skin": sr, "color_ratio": entry["color_ratio"]})

    kept = [r for r in rows if r["skin"] <= SKIN_MAX]
    print(f"候選 {len(rows)} 個 → 濾掉人手 {len(rows)-len(kept)} 個，保留 {len(kept)} 個")
    kept.sort(key=lambda r: -r["strength"])
    top = kept[: args.top]

    CW, CH, PAD, LAB = 400, 225, 8, 30
    cols = 6
    rows_n = (len(top) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (CW + PAD) + PAD, rows_n * (CH + LAB + PAD) + PAD), (16, 16, 18))
    dr = ImageDraw.Draw(sheet)
    for i, r in enumerate(top):
        im = Image.open(os.path.join(CAND_DIR, r["thumb"])).convert("RGB").resize((CW, CH), Image.LANCZOS)
        x = PAD + (i % cols) * (CW + PAD)
        y = PAD + (i // cols) * (CH + LAB + PAD)
        sheet.paste(im, (x, y + LAB))
        dr.text((x, y + 3), f'#{i+1:02d}  {r["strength"]:4.1f}x  {r["file"][:8]} {r["label"]}',
                fill=(255, 214, 0))
    out = os.path.join(BASE, "output/candidates_refined.png")
    sheet.save(out)
    print(f"→ {out}")

    json.dump(top, open(os.path.join(BASE, "output/candidates_refined.json"), "w"),
              ensure_ascii=False, indent=2)
    for i, r in enumerate(top):
        print(f'  #{i+1:02d} {r["strength"]:5.1f}x {r["file"]} {r["label"]}')


if __name__ == "__main__":
    main()
