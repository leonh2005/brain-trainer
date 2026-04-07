#!/usr/bin/env python3
"""
Main pipeline: fetch → deduplicate → analyze → store.
Run via crontab every 15 minutes.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetcher.rss_fetcher import fetch_all_rss
from fetcher.ptt_fetcher import fetch_ptt
from fetcher.reddit_fetcher import fetch_reddit
from analyzer import analyze_all
from storage import DB_PATH, init_db, save_articles, get_unanalyzed, update_analysis, get_irrelevant_examples, set_irrelevant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")

BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID   = "7556217543"
STATE_FILE = Path(__file__).parent / "pipeline_state.json"

# 連續幾次 0 新增才告警
ZERO_INSERT_ALERT_THRESHOLD = 8  # 8次 × 15分鐘 = 2小時


def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"zero_insert_streak": 0, "last_alert_streak": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


def run_pipeline(db_path=DB_PATH):
    logger.info("=== Pipeline start ===")
    init_db(db_path)

    rss    = fetch_all_rss()
    ptt    = fetch_ptt(max_articles=30)
    reddit = fetch_reddit()
    all_articles = rss + ptt + reddit
    logger.info(f"Total fetched: {len(all_articles)}")

    new_count = save_articles(all_articles, db_path)
    logger.info(f"New inserts: {new_count}")

    # 追蹤連續零新增並告警
    state = load_state()
    if new_count == 0:
        state["zero_insert_streak"] += 1
        streak = state["zero_insert_streak"]
        if streak >= ZERO_INSERT_ALERT_THRESHOLD and streak != state.get("last_alert_streak"):
            send_telegram(
                f"⚠️ 財經新聞 pipeline 異常\n"
                f"連續 {streak} 次執行（約 {streak * 15 // 60} 小時）無新文章\n"
                f"RSS feeds 可能停更或網路異常"
            )
            state["last_alert_streak"] = streak
            logger.warning(f"Zero-insert streak: {streak}, alert sent")
    else:
        if state["zero_insert_streak"] >= ZERO_INSERT_ALERT_THRESHOLD:
            send_telegram(f"✅ 財經新聞 pipeline 恢復正常，新增 {new_count} 篇")
        state["zero_insert_streak"] = 0
        state["last_alert_streak"] = 0
    save_state(state)

    unanalyzed = get_unanalyzed(db_path)
    logger.info(f"Unanalyzed: {len(unanalyzed)}")
    if unanalyzed:
        examples = get_irrelevant_examples(limit=5, db_path=db_path)
        if examples:
            logger.info(f"Using {len(examples)} irrelevant examples for few-shot filtering")
        enriched = analyze_all(unanalyzed, irrelevant_examples=examples if examples else None)
        auto_irrelevant = 0
        for a in enriched:
            update_analysis(a["id"], a["score"], a["summary"], a["tags"], db_path)
            if not a.get("relevant", True):
                set_irrelevant(a["id"], True, db_path)
                auto_irrelevant += 1
        logger.info(f"Analyzed: {len(enriched)}, auto-marked irrelevant: {auto_irrelevant}")

    logger.info("=== Pipeline done ===")


if __name__ == "__main__":
    run_pipeline()
