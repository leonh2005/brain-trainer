#!/usr/bin/env python3
"""PS5 Pro / Xbox Series X 二手刊登監控：三來源、每來源獨立 state、逐筆通知。"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from cm_config import CONSOLES, TPE, is_after_cutoff, validate_cutoff
from cm_notify import send
from cm_sources import fetch_carousell, fetch_ptt, fetch_ruten
from cm_state import (
    load_state,
    mark,
    needs_rebaseline,
    record_baseline,
    record_last_success,
    save_state,
    select_new,
)

BASE = Path(__file__).resolve().parent.parent
STATE_DIR = Path(__file__).resolve().parent.parent / "console_monitor_state"
CURRENT_FILE = BASE / "console_monitor_current.json"
LOCK_FILE = BASE / "console_monitor.lock"
LOG_FILE = BASE.parent / "logs" / "console_monitor.log"

SOURCES = {"ruten": fetch_ruten, "ptt": fetch_ptt, "carousell": fetch_carousell}
STALE_GAP = 2 * 3600 + 600
MAX_SEND_PER_RUN = 30


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def state_path(source: str) -> Path:
    return STATE_DIR / f"seen_{source}.json"


def collect_source(source: str) -> list[dict]:
    """同一來源的兩台主机一起抓；單一主机失敗保留另一台結果。"""
    items: list[dict] = []
    for key in CONSOLES:
        try:
            items.extend(SOURCES[source](key))
        except Exception as exc:
            log(f"{source}/{key} 抓取失敗：{type(exc).__name__}: {exc}")
    return items


def run(args: argparse.Namespace) -> None:
    now = int(time.time())
    checked = new = sent = failed = 0
    snapshot: dict[str, dict] = {}

    for source in SOURCES:
        if args.only and source != args.only:
            continue
        path = state_path(source)
        try:
            state = load_state(path)
            items = collect_source(source)
        except Exception as exc:
            log(f"{source} 抓取失敗，state 不變：{type(exc).__name__}: {exc}")
            failed += 1
            continue
        if not items:
            if args.init_baseline and not args.dry_run:
                # 0 筆也要建立空 baseline，否則日後第一次有刊登會被誤當成既有項目
                record_baseline(state, [], now)
                save_state(path, state)
                log(f"{source} 建立空 baseline（0 筆）")
                snapshot[source] = {"count": 0, "baseline": True}
                continue
            log(f"{source} 回傳 0 筆，視為抓取失敗，state 不變")
            failed += 1
            continue

        checked += len(items)
        stale = needs_rebaseline(state, now, STALE_GAP)
        if args.backfill_cutoff:
            candidates = [i for i in items if is_after_cutoff(i.get("posted_ts"))]
        elif args.init_baseline or stale or not state.get("baseline_done"):
            if not args.dry_run:
                record_baseline(state, items, now)
                save_state(path, state)
            reason = "--init-baseline" if args.init_baseline else ("停工後重建" if stale else "首次建立")
            log(f"{source} baseline（{reason}）：{len(items)} 筆，不推播")
            snapshot[source] = {"count": len(items), "baseline": True}
            continue
        else:
            candidates = select_new(state, items)
        new += len(candidates)
        statuses = []
        for item in candidates[:MAX_SEND_PER_RUN]:
            try:
                result = send(item, dry_run=args.dry_run)
                status = result.get("status", "unknown")
                message_id = result.get("message_id")
            except Exception as exc:
                # 非網路錯誤不代表稍後會成功，記為永久失敗並留完整 traceback 供查證
                status = "permanent"
                message_id = None
                log(f"{source} 通知例外 {item['uid']}：{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            if args.dry_run:
                log(f"[dry-run] 將通知 {item['uid']} {item['name']} NT${item['price']:,}")
                statuses.append("dry_run")
                continue
            mark(state, item["uid"], status, now, message_id=message_id)
            save_state(path, state)
            statuses.append(status)
            if status == "delivered":
                sent += 1
            else:
                failed += 1
                log(f"{source} 通知結果 {item['uid']}：{status}")
        if len(candidates) > MAX_SEND_PER_RUN:
            log(f"{source} 本輪候選 {len(candidates)} 筆，只處理前 {MAX_SEND_PER_RUN} 筆")
        if not args.dry_run:
            record_last_success(state, now)
            save_state(path, state)
        snapshot[source] = {"count": len(items), "new": len(candidates), "statuses": statuses}

    if not args.dry_run:
        CURRENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CURRENT_FILE.write_text(json.dumps({
            "updated": datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S"),
            "sources": snapshot,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成：檢查 {checked} 筆、新增 {new} 筆、送出 {sent} 筆、失敗 {failed} 筆"
        + ("（dry-run）" if args.dry_run else ""))


def main(argv: list[str] | None = None) -> int:
    validate_cutoff()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="不送通知、不寫 state")
    parser.add_argument("--only", choices=tuple(SOURCES), help="只跑單一來源")
    parser.add_argument("--init-baseline", action="store_true", help="明確重建 baseline，不推播")
    parser.add_argument("--backfill-cutoff", action="store_true", help="補推門檻後所有刊登（人工專用）")
    args = parser.parse_args(argv)

    lock = open(LOCK_FILE, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("上一輪尚未結束，略過本輪")
        lock.close()
        return 0
    try:
        run(args)
    except Exception as exc:
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
