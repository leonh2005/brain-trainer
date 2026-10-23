#!/usr/bin/env python3
"""Abacus 路徑的壓縮 A/B 對照報表。

讀 proxy-abacus.log（8791 instance），依 AB:on（奇數日壓縮）與 AB:ctrl（偶數日原樣）分組，
比較每次請求的平均輸入／輸出 token 與壓縮省下的字元。

用法：
    python3 ab_report.py            # 全部紀錄
    python3 ab_report.py 2026-10-23 # 只看指定日期之後
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from statistics import mean

LOG_PATH = Path(__file__).resolve().parent / "proxy-abacus.log"

USAGE_RE = re.compile(
    r"^(?P<date>\S+) \S+ \[usage\] (?P<group>AB:\w+|-) "
    r"in=(?P<in>\d+|-) cache_read=(?P<cr>\d+|-) cache_create=(?P<cc>\d+|-) out=(?P<out>\d+|-)"
)
COMPACT_RE = re.compile(r"\[compact\] AB:on .*?省 (?P<chars>[\d,]+) 字元")


def _num(s: str) -> int:
    return 0 if s == "-" else int(s)


def main() -> None:
    since = sys.argv[1] if len(sys.argv) > 1 else ""
    if not LOG_PATH.exists():
        print(f"找不到 {LOG_PATH}")
        return

    groups: dict[str, list[tuple[int, int]]] = {"AB:on": [], "AB:ctrl": []}
    saved: dict[str, int] = {"AB:on": 0}
    for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if since and line[:10] < since:
            continue
        if m := USAGE_RE.match(line):
            if m["group"] not in groups:
                continue
            total_in = _num(m["in"]) + _num(m["cr"]) + _num(m["cc"])
            groups[m["group"]].append((total_in, _num(m["out"])))
        elif m := COMPACT_RE.search(line):
            saved["AB:on"] += int(m["chars"].replace(",", ""))

    print(f"{'組別':<9}{'請求數':>7}{'平均總輸入 tok':>16}{'平均輸出 tok':>14}{'總輸入 tok':>14}")
    for name, rows in groups.items():
        if not rows:
            print(f"{name:<9}{0:>7}{'-':>16}{'-':>14}{'-':>14}")
            continue
        ins = [r[0] for r in rows]
        outs = [r[1] for r in rows]
        print(f"{name:<9}{len(rows):>7}{mean(ins):>16,.0f}{mean(outs):>14,.0f}{sum(ins):>14,}")
    print(f"壓縮省下（AB:on）：{saved['AB:on']:,} 字元")


if __name__ == "__main__":
    main()
