#!/usr/bin/env python3
"""
Main pipeline: fetch → deduplicate → analyze → store.
Run via crontab every 15 minutes.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetcher.rss_fetcher import fetch_all_rss
from fetcher.ptt_fetcher import fetch_ptt
from fetcher.reddit_fetcher import fetch_reddit
from analyzer import analyze_all
from storage import DB_PATH, init_db, save_articles, get_unanalyzed, update_analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def run_pipeline(db_path=DB_PATH):
    logger.info("=== Pipeline start ===")
    init_db(db_path)

    rss = fetch_all_rss()
    ptt = fetch_ptt(max_articles=30)
    reddit = fetch_reddit()
    all_articles = rss + ptt + reddit
    logger.info(f"Total fetched: {len(all_articles)}")

    save_articles(all_articles, db_path)

    unanalyzed = get_unanalyzed(db_path)
    logger.info(f"Unanalyzed: {len(unanalyzed)}")
    if unanalyzed:
        enriched = analyze_all(unanalyzed)
        for a in enriched:
            update_analysis(a["id"], a["score"], a["summary"], a["tags"], db_path)
        logger.info(f"Analyzed: {len(enriched)}")

    logger.info("=== Pipeline done ===")


if __name__ == "__main__":
    run_pipeline()
