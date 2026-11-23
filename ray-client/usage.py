"""查詢 Ray 資源池的使用率，判斷是否值得保留。"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LOG_FILE = os.path.expanduser("~/CCProject/logs/ray_usage.log")
TPE = timezone(timedelta(hours=8))


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TPE)
    return parsed.astimezone(TPE)


def load() -> tuple[list[dict], int]:
    if not os.path.exists(LOG_FILE):
        return [], 0
    rows, bad = [], 0
    for line in open(LOG_FILE, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return rows, bad


def report(days: int = 30) -> None:
    all_rows, bad_lines = load()
    cutoff = datetime.now(TPE) - timedelta(days=days)
    rows, bad_ts = [], 0
    for r in all_rows:
        ts = _parse_ts(r.get("ts"))
        if ts is None or ts < cutoff:
            bad_ts += 1 if ts is None else 0
            continue
        rows.append(r)

    daily: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "tasks": 0, "seconds": 0.0, "errors": 0})
    for r in rows:
        if r.get("event") != "finish":
            continue
        day = r.get("ts", "")[:10]
        daily[day]["jobs"] += 1
        daily[day]["tasks"] += int(r.get("tasks", 0) or 0)
        daily[day]["seconds"] += float(r.get("duration_s", 0) or 0)
        daily[day]["errors"] += 1 if r.get("status") != "ok" else 0

    print(f"最近 {days} 天：{len(daily)} 天有使用")
    print(f"{'日期':<12} {'次數':>5} {'任務':>7} {'秒數':>9} {'錯誤':>5}")
    for day in sorted(daily):
        v = daily[day]
        print(f"{day:<12} {v['jobs']:>5} {v['tasks']:>7} {v['seconds']:>9.1f} {v['errors']:>5}")

    total_seconds = sum(v["seconds"] for v in daily.values())
    total_jobs = sum(v["jobs"] for v in daily.values())
    print(f"\n合計：{total_jobs} 次、{total_seconds:.1f} 秒、{len(daily)} 天")
    if bad_lines or bad_ts:
        print(f"警告：略過 {bad_lines} 行無效 JSON、{bad_ts} 筆無效時間")
    if total_jobs < 10 or total_seconds < 600:
        print("判讀：低使用量，建議關掉常駐以省資源；需要時再開。")
    else:
        print("判讀：有一定使用量，值得保留常駐。")


if __name__ == "__main__":
    report(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
