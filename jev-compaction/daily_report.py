#!/usr/bin/env python3
"""Jev context 壓縮的每日成果報告。

讀 proxy.log，統計指定日期（預設昨天）的壓縮成效，透過 Telegram 推播。

用法：
    python3 daily_report.py            # 昨天
    python3 daily_report.py 2026-09-24 # 指定日期
    python3 daily_report.py --dry      # 只印出，不推播
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
LOG_PATH = HOME / "CCProject/jev-compaction/proxy.log"
TOKEN_PATH = HOME / "CCProject/.secrets/telegram_token.txt"
CHAT_ID = "7556217543"

# 範例：
# 2026-09-24 12:27:08,614 [compact] dry-run | 候選 120 筆 → 移除 67 筆，省 138,285 字元 (66.8%)，快取命中 119，批次 1
LINE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2}),\d+ \[compact\] "
    r"(?P<mode>dry-run \| )?"
    r"候選 (?P<cand>\d+) 筆 → 移除 (?P<drop>\d+) 筆，"
    r"省 (?P<chars>[\d,]+) 字元 \((?P<pct>[\d.]+)%\)，"
    r"快取命中 (?P<cached>\d+)，批次 (?P<batches>\d+)"
)


def parse_log(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            d = m.groupdict()
            rows.append({
                "date": d["date"],
                "time": d["time"],
                "dry_run": bool(d["mode"]),
                "candidates": int(d["cand"]),
                "dropped": int(d["drop"]),
                "chars": int(d["chars"].replace(",", "")),
                "pct": float(d["pct"]),
                "cached": int(d["cached"]),
                "batches": int(d["batches"]),
            })
    return rows


def summarize(rows: list[dict], day: str) -> str:
    if not rows:
        return (f"📊 Jev 壓縮日報（{day}）\n\n"
                f"昨天沒有壓縮紀錄。\n"
                f"可能是沒有使用 Claude Code，或 proxy 沒在跑。")

    n = len(rows)
    with_cand = [r for r in rows if r["candidates"] > 0]
    total_cand = sum(r["candidates"] for r in rows)
    total_drop = sum(r["dropped"] for r in rows)
    total_chars = sum(r["chars"] for r in rows)
    total_cached = sum(r["cached"] for r in rows)
    total_eval = total_cand + total_cached
    is_dry = any(r["dry_run"] for r in rows)

    lines = [f"📊 Jev 壓縮日報（{day}）", ""]
    lines.append(f"請求　　{n} 次（其中 {len(with_cand)} 次有可壓縮內容）")

    if total_cand == 0:
        lines.append("")
        lines.append("昨天沒有產生可壓縮的舊工具輸出。")
        return "\n".join(lines)

    # 每筆的 pct 是「該次請求省下的比例」，反推原始字元數才能算整體平均
    total_original = sum(r["chars"] / (r["pct"] / 100.0) for r in rows if r["pct"] > 0)
    pct = 100 * total_chars / max(1, total_original)
    lines.append(f"候選　　{total_cand:,} 筆 → 移除 {total_drop:,} 筆（{100*total_drop/max(1,total_cand):.1f}%）")
    lines.append(f"省下　　{total_chars:,} 字元（約 {total_chars//4:,} tokens）")
    lines.append(f"平均壓縮率　{pct:.1f}%")
    if total_eval:
        lines.append(f"快取命中　{total_cached:,}/{total_eval:,}（{100*total_cached/max(1,total_eval):.0f}%，省下重複的判斷費用）")

    lines.append("")
    if is_dry:
        lines.append("⚠️ 目前是影子模式——只評估、沒有真的壓縮。")
        lines.append("內容一個字都沒被動，這是刻意的觀察期。")

    # 峰值時段
    by_hour: dict[str, int] = {}
    for r in rows:
        h = r["time"][:2]
        by_hour[h] = by_hour.get(h, 0) + r["chars"]
    if by_hour:
        top = sorted(by_hour.items(), key=lambda x: -x[1])[:3]
        lines.append("")
        lines.append("最省時段：" + "、".join(f"{h}點({c//1000}k)" for h, c in top if c > 0))

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    import json
    import urllib.request
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    payload = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:  # noqa: BLE001
        print(f"Telegram 推播失敗：{e}", file=sys.stderr)
        return False


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    day = args[0] if args else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    rows = [r for r in parse_log(LOG_PATH) if r["date"] == day]
    text = summarize(rows, day)
    print(text)
    if dry:
        return 0
    return 0 if send_telegram(text) else 1


if __name__ == "__main__":
    sys.exit(main())
