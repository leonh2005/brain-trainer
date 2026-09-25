#!/usr/bin/env python3
"""依「墨墨在畫面中的佔比」重新篩選候選，找出與鏡頭距離適中的片段。

GeekMagic 裝置只有 240x240，主體貼滿畫面反而不好看——需要看得到姿勢與
周圍空間。用去背模型算出每個候選縮圖的前景佔比，篩出適中的區間。

用法：
    ./venv/bin/python rank_framing.py [--min 0.12] [--max 0.42]
"""
import argparse
import json
import os

import numpy as np
import torch
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
CAND = os.path.join(BASE, "output/candidates")
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.12, help="前景佔比下限")
    ap.add_argument("--max", type=float, default=0.42, help="前景佔比上限")
    ap.add_argument("--top", type=int, default=30)
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

    # 從完整掃描結果攤平出所有候選，並先濾掉人手主導的（與 refine_candidates 同規則）
    raw = json.load(open(os.path.join(BASE, "output/scan_results.json")))
    rows = []
    for entry in raw:
        for m in entry["moments"]:
            p = os.path.join(CAND, m["thumb"])
            if not os.path.exists(p):
                continue
            a = np.asarray(Image.open(p).convert("RGB")).astype(np.int16)
            r_, g_, b_ = a[..., 0], a[..., 1], a[..., 2]
            skin = ((r_ > 95) & (g_ > 40) & (b_ > 20) &
                    ((r_ - b_) > 15) & (np.abs(r_ - g_) > 15) & (r_ > g_) & (r_ > b_)).mean()
            if skin > 0.06:
                continue
            rows.append({"file": entry["file"], "label": m["label"],
                         "strength": m["strength"], "thumb": m["thumb"]})
    print(f"濾掉人手後 {len(rows)} 個候選，開始估算佔比…", flush=True)

    out = []
    for i, r in enumerate(rows, 1):
        p = os.path.join(CAND, r["thumb"])
        im = Image.open(p).convert("RGB")
        with torch.no_grad():
            pred = model(tf(im).unsqueeze(0).to(DEVICE))[-1].sigmoid().cpu()[0, 0].numpy()
        cover = float((pred > 0.5).mean())
        out.append({**r, "cover": round(cover, 3)})
        if i % 40 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    inside = [r for r in out if args.min <= r["cover"] <= args.max]
    # 佔比越接近區間中值越好；同分再比動作強度
    mid = (args.min + args.max) / 2
    inside.sort(key=lambda r: (abs(r["cover"] - mid) / mid, -r["strength"]))
    top = inside[: args.top]

    print(f"\n共 {len(out)} 個候選，佔比在 {args.min}-{args.max} 的有 {len(inside)} 個")
    CW, CH, PAD, LAB = 400, 225, 8, 30
    cols = 6
    rows_n = (len(top) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (CW + PAD) + PAD, rows_n * (CH + LAB + PAD) + PAD), (16, 16, 18))
    dr = ImageDraw.Draw(sheet)
    for i, r in enumerate(top):
        im = Image.open(os.path.join(CAND, r["thumb"])).convert("RGB").resize((CW, CH), Image.LANCZOS)
        x = PAD + (i % cols) * (CW + PAD)
        y = PAD + (i // cols) * (CH + LAB + PAD)
        sheet.paste(im, (x, y + LAB))
        dr.text((x, y + 3), f'#{i+1:02d} 佔{r["cover"]*100:.0f}% {r["strength"]:4.1f}x '
                            f'{r["file"][:8]} {r["label"]}', fill=(0, 255, 140))
    out_png = os.path.join(BASE, "output/candidates_framing.png")
    sheet.save(out_png)
    print(f"→ {out_png}")
    json.dump(top, open(os.path.join(BASE, "output/candidates_framing.json"), "w"),
              ensure_ascii=False, indent=2)
    for i, r in enumerate(top[:20]):
        print(f'  #{i+1:02d} 佔{r["cover"]*100:3.0f}% {r["strength"]:5.1f}x {r["file"]} {r["label"]}')


if __name__ == "__main__":
    main()
