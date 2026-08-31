#!/usr/bin/env python3
"""批次為 /tmp/momo_filelist.tsv 中列出的檔案產生拼接縮圖（跳過已存在的）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from make_montages import make_montage, OUT_DIR

with open('/tmp/momo_filelist.tsv') as f:
    rows = [line.rstrip('\n').split('\t') for line in f if line.strip()]

total = len(rows)
for i, (subdir, filename) in enumerate(rows):
    base = os.path.splitext(filename)[0]
    out_path = os.path.join(OUT_DIR, f"{base}.jpg")
    if os.path.exists(out_path):
        continue
    make_montage(filename, subdir)
    if (i + 1) % 20 == 0:
        print(f"進度 {i+1}/{total}")

print(f"完成，共 {total} 個")
