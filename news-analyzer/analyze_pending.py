#!/usr/bin/env python3
"""分析未處理的新聞：Jev 先篩相關性，相關的才送 LLM 生成摘要與情緒。

原本的 analyzer.analyze_all 已經沒有排程在呼叫（分析停擺），這支取代它，
並在 LLM 之前加一道 Jev 篩選關卡：

    fetch → [Jev 篩相關性] → 相關的 → [gpt-4o-mini 生成 score/summary/tags] → DB

效益：Jev 又快又便宜（22 筆/秒、約 LLM 的 1/8 成本），先濾掉約 20-25% 的雜訊，
讓後面要花錢的 LLM 只處理真正相關的新聞。

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

import analyzer
import jev_filter
from storage import DB_PATH, get_conn, update_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("analyze_pending")

# Jev 判不相關時寫入的樣式：與原本 LLM 的行為一致（空摘要、中性分數）
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


def _classify_with_retry(titles: list[str], max_attempts: int = 5,
                         base_delay: float = 10.0) -> list[dict]:
    """Jev 過載時指數退避重試。適合批次場景（互動場景該 fail-open）。"""
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return jev_filter.classify_all(titles)
        except jev_filter.JevFilterError as e:
            last = e
            delay = base_delay * (2 ** attempt)      # 10s → 20s → 40s → 80s
            if attempt < max_attempts - 1:
                logger.warning("Jev 失敗（第 %d/%d 次），%.0f 秒後重試：%s",
                               attempt + 1, max_attempts, delay, str(e)[:90])
                time.sleep(delay)
    raise last  # type: ignore[misc]


def run(days: int, limit: int | None, dry: bool, db_path=DB_PATH) -> dict:
    stats = {
        "pending": 0, "jev_relevant": 0, "jev_irrelevant": 0,
        "llm_done": 0, "llm_failed": 0, "jev_seconds": 0.0, "llm_seconds": 0.0,
    }

    pending = fetch_pending(days, limit, db_path)
    stats["pending"] = len(pending)
    if not pending:
        logger.info("沒有待分析的新聞（最近 %d 天）", days)
        return stats
    logger.info("待分析 %d 筆（最近 %d 天）", len(pending), days)

    # ── 第一關：Jev 篩相關性 ──────────────────────────────
    # 批次場景要重試，不能像 proxy 那樣快速放棄：
    # 補跑是一大批工作，Jev 一時過載（529）就整個退出會讓整輪白跑。
    t0 = time.time()
    try:
        verdicts = _classify_with_retry([a["title"] for a in pending])
    except jev_filter.JevFilterError as e:
        logger.error("Jev 重試後仍失敗，這輪不處理：%s", e)
        return stats
    stats["jev_seconds"] = time.time() - t0

    relevant, irrelevant = [], []
    for art, v in zip(pending, verdicts):
        (relevant if v["relevant"] else irrelevant).append(art)
    stats["jev_relevant"] = len(relevant)
    stats["jev_irrelevant"] = len(irrelevant)
    logger.info("Jev 篩選：相關 %d、不相關 %d（耗時 %.1fs，%.1f 筆/秒）",
                len(relevant), len(irrelevant), stats["jev_seconds"],
                len(pending) / max(stats["jev_seconds"], 0.01))

    if dry:
        logger.info("--dry：不寫入，結束")
        return stats

    # 不相關的直接標記（省下 LLM 呼叫）
    with get_conn(db_path) as conn:
        for a in irrelevant:
            conn.execute(
                "UPDATE articles SET score=?, summary='', tags='[]', analyzed_at=datetime('now') WHERE id=?",
                (NEUTRAL_SCORE, a["id"]),
            )
        conn.commit()
    logger.info("已標記 %d 筆為不相關（未送 LLM）", len(irrelevant))

    # ── 第二關：相關的才送 LLM 生成 ──────────────────────
    if relevant:
        t1 = time.time()
        try:
            examples = None
            try:
                from storage import get_irrelevant_examples
                examples = get_irrelevant_examples(5, db_path)
            except Exception:  # noqa: BLE001
                pass
            enriched = analyzer.analyze_all(relevant, examples)
        except Exception as e:  # noqa: BLE001
            logger.error("LLM 分析失敗：%s", e)
            stats["llm_failed"] = len(relevant)
            return stats
        stats["llm_seconds"] = time.time() - t1
        for a in enriched:
            try:
                update_analysis(a["id"], a.get("score", NEUTRAL_SCORE),
                                a.get("summary", ""), a.get("tags", []), db_path)
                stats["llm_done"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("寫入失敗 id=%s: %s", a.get("id"), e)
                stats["llm_failed"] += 1
        logger.info("LLM 完成 %d 筆（耗時 %.1fs）", stats["llm_done"], stats["llm_seconds"])

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
    print(f"  Jev 判不相關  {s['jev_irrelevant']:,}  ← 省下的 LLM 呼叫")
    print(f"  LLM 完成      {s['llm_done']:,}")
    print(f"  Jev 耗時      {s['jev_seconds']:.1f}s")
    print(f"  LLM 耗時      {s['llm_seconds']:.1f}s")
    if s["pending"]:
        saved = 100 * s["jev_irrelevant"] / s["pending"]
        print(f"  LLM 呼叫省下  {saved:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
