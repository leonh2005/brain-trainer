#!/usr/bin/env python3
"""分析未處理的新聞：兩階段 Jev 判斷（相關性 → 多空），完全不呼叫 LLM。

    未分析新聞 ─┬─[Jev 判相關性]─ 不相關 → 標記 auto_irrelevant，不進多空統計
                └──────────────── 相關   → [Jev 判多空] → score 10/5/1

原本送 gpt-4o-mini 產出 score/summary/tags；改成 Jev 後不再生成摘要，
summary/tags 一律留空。前端統計改以 auto_irrelevant 欄位排除無關新聞，
不再依賴「score=5 且無摘要」這種間接訊號。

用法：
    python3 analyze_pending.py --days 7          # 只處理最近 7 天
    python3 analyze_pending.py --days 7 --limit 500
    python3 analyze_pending.py --days 7 --dry    # 只評估不寫入
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_filter
from storage import DB_PATH, get_conn, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("analyze_pending")

# 沒有明確方向的預設分數
NEUTRAL_SCORE = 5


def fetch_pending(days: int, limit: int | None, db_path) -> list[dict]:
    """取出未分析的新聞（限最近 N 天）。"""
    sql = """SELECT id, source, title, content FROM articles
             WHERE score IS NULL
               AND fetched_at >= datetime('now', ?)
             ORDER BY fetched_at DESC"""
    params: list = [f"-{days} days"]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with get_conn(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _classify_with_retry(titles: list[str], classify_fn, label: str,
                         max_attempts: int = 5, base_delay: float = 10.0) -> list[dict]:
    """Jev 過載時指數退避重試。適合批次場景（互動場景該 fail-open）。"""
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return classify_fn(titles)
        except jev_filter.JevFilterError as e:
            last = e
            delay = base_delay * (2 ** attempt)      # 10s → 20s → 40s → 80s
            if attempt < max_attempts - 1:
                logger.warning("%s 失敗（第 %d/%d 次），%.0f 秒後重試：%s",
                               label, attempt + 1, max_attempts, delay, str(e)[:90])
                time.sleep(delay)
    raise last  # type: ignore[misc]


def _write_scores(rows: list[tuple[int, int, int]], db_path) -> None:
    """寫入 (id, score, auto_irrelevant)。Jev 不生成文字，摘要與標籤一律清空。"""
    with get_conn(db_path) as conn:
        for article_id, score, auto_irrelevant in rows:
            conn.execute(
                "UPDATE articles SET score=?, summary='', tags='[]', "
                "analyzed_at=datetime('now'), auto_irrelevant=? WHERE id=?",
                (score, auto_irrelevant, article_id),
            )
        conn.commit()


def run(days: int, limit: int | None, dry: bool, db_path=DB_PATH) -> dict:
    # 確保 schema 就緒（新增 auto_irrelevant 欄位的 migration 在這裡觸發，
    # 不依賴 pipeline 先跑過）
    init_db(db_path)

    stats = {
        "pending": 0, "jev_relevant": 0, "jev_irrelevant": 0,
        "sentiment_done": 0, "relevant_seconds": 0.0, "sentiment_seconds": 0.0,
    }

    pending = fetch_pending(days, limit, db_path)
    stats["pending"] = len(pending)
    if not pending:
        logger.info("沒有待分析的新聞（最近 %d 天）", days)
        return stats
    logger.info("待分析 %d 筆（最近 %d 天）", len(pending), days)

    # ── 第一關：相關性 ──────────────────────────────────────
    # 批次場景要重試，不能像 proxy 那樣快速放棄：
    # 補跑是一大批工作，Jev 一時過載（529）就整個退出會讓整輪白跑。
    t0 = time.time()
    try:
        verdicts = _classify_with_retry([a["title"] for a in pending],
                                        jev_filter.classify_all, "Jev 相關性")
    except jev_filter.JevFilterError as e:
        logger.error("Jev 相關性重試後仍失敗，這輪不處理：%s", e)
        return stats
    stats["relevant_seconds"] = time.time() - t0

    relevant, irrelevant = [], []
    for art, v in zip(pending, verdicts):
        (relevant if v["relevant"] else irrelevant).append(art)
    stats["jev_relevant"] = len(relevant)
    stats["jev_irrelevant"] = len(irrelevant)
    logger.info("Jev 相關性：相關 %d、不相關 %d（耗時 %.1fs，%.1f 筆/秒）",
                len(relevant), len(irrelevant), stats["relevant_seconds"],
                len(pending) / max(stats["relevant_seconds"], 0.01))

    if dry:
        logger.info("--dry：不寫入，結束")
        return stats

    # 不相關：標記 auto_irrelevant，不進多空統計
    if irrelevant:
        _write_scores([(a["id"], NEUTRAL_SCORE, 1) for a in irrelevant], db_path)
        logger.info("已標記 %d 筆為無關（不進多空統計）", len(irrelevant))

    # ── 第二關：多空 ────────────────────────────────────────
    if relevant:
        t1 = time.time()
        try:
            sentiments = _classify_with_retry([a["title"] for a in relevant],
                                              jev_filter.classify_sentiment_all, "Jev 多空")
        except jev_filter.JevFilterError as e:
            logger.error("Jev 多空重試後仍失敗，這批不寫入：%s", e)
            return stats
        stats["sentiment_seconds"] = time.time() - t1

        rows = [(a["id"],
                 jev_filter.SENTIMENT_SCORE.get(s["sentiment"], NEUTRAL_SCORE),
                 0)
                for a, s in zip(relevant, sentiments)]
        _write_scores(rows, db_path)
        stats["sentiment_done"] = len(rows)
        logger.info("多空完成 %d 筆（耗時 %.1fs）", len(rows), stats["sentiment_seconds"])

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="只處理最近 N 天（預設 7）")
    ap.add_argument("--limit", type=int, default=None, help="最多處理幾筆")
    ap.add_argument("--dry", action="store_true", help="只評估不寫入")
    args = ap.parse_args()

    s = run(args.days, args.limit, args.dry)
    print("\n=== 統計 ===")
    print(f"  待分析        {s['pending']:,}")
    print(f"  Jev 判相關    {s['jev_relevant']:,}")
    print(f"  Jev 判不相關  {s['jev_irrelevant']:,}  ← 不進多空統計")
    print(f"  多空完成      {s['sentiment_done']:,}")
    print(f"  相關性耗時    {s['relevant_seconds']:.1f}s")
    print(f"  多空耗時      {s['sentiment_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
