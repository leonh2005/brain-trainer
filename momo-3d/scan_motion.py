#!/usr/bin/env python3
"""掃描監視器影片，找出「彩色 + 墨墨有動作 + 曝光穩定」的時段。

影片很長（約 30-40 分鐘/支），全片逐格處理不划算，所以降到 160x90 以
1fps 抽樣。三個訊號：

  sat     色彩飽和度 —— 區分白天彩色與紅外線黑白，紅外線畫面做視差效果差
  bri     平均亮度   —— 用來偵測日夜模式切換造成的整格過曝
  motion  相鄰格差異 —— 動作強度；亮度劇變的格不計，否則切換會被誤判成大動作

時間軸一律走 ffmpeg 的 fps 濾鏡，不用 -ss：實測這批檔案 -ss 定位會嚴重
偏移（差到兩小時），fps 濾鏡則與畫面內嵌時間戳一致。

用法：
    ./venv/bin/python scan_motion.py <影片路徑> [--top 8] [--sat-min 18]
"""
import argparse
import os
import subprocess

import numpy as np

W, H, FPS = 160, 90, 1


def analyze(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """回傳 (飽和度, 亮度, 動作量)，皆為每秒一筆。"""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    if n < 2:
        raise SystemExit("影片太短或解碼失敗")
    f = np.frombuffer(raw[: n * W * H * 3], dtype=np.uint8).reshape(n, H, W, 3).astype(np.float32)

    sat = (f.max(axis=3) - f.min(axis=3)).mean(axis=(1, 2))
    bri = f.mean(axis=(1, 2, 3))
    diff = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3))
    bri_jump = np.abs(np.diff(bri))
    motion = np.where(bri_jump > 20, 0.0, diff)     # 亮度劇變 = 模式切換，不算動作
    motion = np.concatenate([[0.0], motion])
    return sat, bri, motion


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--sat-min", type=float, default=18.0,
                    help="飽和度門檻，低於此視為紅外線黑白")
    args = ap.parse_args()

    sat, bri, motion = analyze(args.video)
    n = len(motion)
    usec = sat > args.sat_min
    print(f"{os.path.basename(args.video)}  長度 {n/60:.0f} 分鐘")
    print(f"  彩色時段占比 {usec.mean()*100:.0f}%  "
          f"（飽和度中位數 {np.median(sat):.0f}，門檻 {args.sat_min:.0f}）")
    print(f"  動作量 平均 {motion.mean():.2f}  最大 {motion.max():.2f}")

    # 只在彩色時段找動作，取樣本彼此至少相隔 30 秒
    score = np.where(usec, motion, 0.0)
    order = np.argsort(score)[::-1]
    picked: list[int] = []
    for i in order:
        if score[i] <= 0:
            break
        if all(abs(int(i) - p) > 30 for p in picked):
            picked.append(int(i))
        if len(picked) >= args.top:
            break

    if not picked:
        print("\n彩色時段內沒有明顯動作。")
        return

    print(f"\n彩色時段中動作最明顯的 {len(picked)} 處：")
    for sec in sorted(picked):
        mm, ss = divmod(sec, 60)
        seg = score[max(0, sec - 2): sec + 3].mean()
        base = motion[usec].mean() if usec.any() else 1.0
        print(f"  {mm:02d}:{ss:02d}  動作 {seg:.2f}（彩色段平均的 "
              f"{seg / max(base, 1e-6):.1f} 倍）")


if __name__ == "__main__":
    main()
