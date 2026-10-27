#!/usr/bin/env python3
"""
DB 修剪：articles 只保留最近 RETENTION_DAYS 天的新聞，其餘刪掉並 VACUUM 回收空間。

保留 35 天是為了配合 last30days-web/app.py 的 refresh_corpus()——它匯出近 35 天
的新聞成每日 md 語料庫給搜尋引擎，砍到 30 天會讓最舊 5 天的語料消失。

人工標記為「不相關」的標題會先搬進 irrelevant_examples 表（few-shot 負樣本池），
修剪後 articles 裡不再有不相關文章，few-shot 仍能從池子取得參考範例。

用法：
    python3 prune_db.py                # 修剪（預設 35 天）
    python3 prune_db.py --days 60      # 自訂保留天數
    python3 prune_db.py --dry-run      # 只統計不刪
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import DB_PATH, EXAMPLES_TABLE_SQL, get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prune_db")

RETENTION_DAYS = 35
MAX_EXAMPLES = 500

# 文章的有效日期：published_at 無法解析、為 NULL 或落在未來時，退回 fetched_at 判斷新舊。
# 連 fetched_at 都無法解析（壞資料）時視為 1970，確保這種幽靈文章不會永遠留著。
EFFECTIVE_DATE_SQL = """
    COALESCE(
        datetime(CASE
            WHEN date(published_at) IS NULL THEN fetched_at
            WHEN date(published_at) > date('now') THEN fetched_at
            ELSE published_at
        END),
        '1970-01-01 00:00:00'
    )
"""


def ensure_examples_table(conn) -> None:
    conn.execute(EXAMPLES_TABLE_SQL)
    conn.commit()


def sync_examples(conn) -> int:
    """把人工標記為不相關的文章搬進範例池，回傳實際新增筆數。

    marked_at 是「人工標記時間」；修剪前就標記好的舊資料沒有記錄時間，
    以 fetched_at 近似。新標記的由 storage.set_irrelevant() 寫入真實時間。"""
    conn.execute(
        """INSERT OR IGNORE INTO irrelevant_examples (title, source, marked_at)
           SELECT title, source, fetched_at FROM articles WHERE irrelevant = 1"""
    )
    inserted = conn.execute("SELECT changes()").fetchone()[0]
    # 只留最新的 MAX_EXAMPLES 筆，避免池子無限膨脹
    conn.execute(
        """DELETE FROM irrelevant_examples WHERE title NOT IN (
               SELECT title FROM irrelevant_examples ORDER BY marked_at DESC LIMIT ?
           )""",
        (MAX_EXAMPLES,),
    )
    conn.commit()
    return inserted


def prune_old_articles(conn, days: int) -> int:
    """刪除有效日期早於保留窗的文章，回傳刪除筆數。"""
    cur = conn.execute(
        f"DELETE FROM articles WHERE {EFFECTIVE_DATE_SQL} < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
    return cur.rowcount


def run_prune(days: int = RETENTION_DAYS, db_path: str = DB_PATH, dry_run: bool = False) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn = get_conn(db_path)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        ensure_examples_table(conn)
        before = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        examples = conn.execute("SELECT COUNT(*) FROM irrelevant_examples").fetchone()[0]

        if dry_run:
            to_delete = conn.execute(
                f"SELECT COUNT(*) FROM articles WHERE {EFFECTIVE_DATE_SQL} < datetime('now', ?)",
                (f"-{days} days",),
            ).fetchone()[0]
            would_sync = conn.execute(
                """SELECT COUNT(*) FROM articles WHERE irrelevant = 1
                   AND title NOT IN (SELECT title FROM irrelevant_examples)"""
            ).fetchone()[0]
            return {"before": before, "deleted": to_delete, "after": before,
                    "examples": examples, "new_examples": would_sync,
                    "cutoff": cutoff.isoformat()}

        new_examples = sync_examples(conn)
        deleted = prune_old_articles(conn, days)
        after = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        examples = conn.execute("SELECT COUNT(*) FROM irrelevant_examples").fetchone()[0]
        conn.execute("VACUUM")
        return {"before": before, "deleted": deleted, "after": after,
                "examples": examples, "new_examples": new_examples,
                "cutoff": cutoff.isoformat()}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="修剪 news.db，只保留最近 N 天的新聞")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS, help=f"保留天數（預設 {RETENTION_DAYS}）")
    parser.add_argument("--dry-run", action="store_true", help="只統計不刪除")
    args = parser.parse_args()

    result = run_prune(days=args.days, dry_run=args.dry_run)
    logger.info(
        "articles %d → %d（刪除 %d，範例池 %d 筆）cutoff=%s",
        result["before"], result["after"], result["deleted"],
        result["examples"], result["cutoff"],
    )


if __name__ == "__main__":
    main()
