import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")

# 人工標記「不相關」的標題池。articles 會被定期修剪成只剩近期文章，
# 這個表讓 few-shot 負樣本不受修剪影響。
EXAMPLES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS irrelevant_examples (
        title     TEXT PRIMARY KEY,
        source    TEXT,
        marked_at TEXT
    )
"""


def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path=DB_PATH):
    with get_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT NOT NULL,
                title           TEXT NOT NULL,
                url             TEXT UNIQUE NOT NULL,
                content         TEXT,
                published_at    DATETIME,
                fetched_at      DATETIME NOT NULL,
                score           INTEGER,
                summary         TEXT,
                tags            TEXT,
                analyzed_at     DATETIME,
                irrelevant      INTEGER NOT NULL DEFAULT 0,
                auto_irrelevant INTEGER NOT NULL DEFAULT 0
            )
        """)
        # migration：舊 DB 補欄位
        cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
        if "irrelevant" not in cols:
            conn.execute("ALTER TABLE articles ADD COLUMN irrelevant INTEGER NOT NULL DEFAULT 0")
        if "auto_irrelevant" not in cols:
            conn.execute("ALTER TABLE articles ADD COLUMN auto_irrelevant INTEGER NOT NULL DEFAULT 0")
        conn.execute(EXAMPLES_TABLE_SQL)
        conn.commit()


def save_articles(articles, db_path=DB_PATH):
    """Insert articles, ignore duplicates by URL. Returns count of newly inserted rows."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        conn.executemany(
            """INSERT OR IGNORE INTO articles
               (source, title, url, content, published_at, fetched_at)
               VALUES (:source, :title, :url, :content, :published_at, :fetched_at)""",
            [
                {
                    "source": a["source"],
                    "title": a["title"],
                    "url": a["url"],
                    "content": a.get("content"),
                    "published_at": a.get("published_at"),
                    "fetched_at": now,
                }
                for a in articles
            ],
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        return after - before


def get_unanalyzed(db_path=DB_PATH):
    """Return up to 100 articles with no score yet."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, source, title, content FROM articles WHERE score IS NULL ORDER BY fetched_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]


def get_irrelevant_examples(limit=5, db_path=DB_PATH):
    """回傳人工標記為「不相關」的文章標題。

    這是**人工標註的 ground truth**（評測模型相關性判斷用）。articles 會被定期修剪成
    只剩近期文章，所以人工標記的標題另外存在 irrelevant_examples 池裡；
    池子空時退回 articles 內的標記。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT title FROM irrelevant_examples ORDER BY RANDOM() LIMIT ?", (limit,)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT title FROM articles WHERE irrelevant = 1 ORDER BY RANDOM() LIMIT ?",
                (limit,),
            ).fetchall()
    return [r["title"] for r in rows]


def set_irrelevant(article_id, value, db_path=DB_PATH):
    """標記／取消標記文章為不相關。

    標記時同步寫入 irrelevant_examples 範例池（記錄真實標記時間），
    取消標記時從池子移除——池子裡的標題都被當成「不相關」範例餵給分析器。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("UPDATE articles SET irrelevant=? WHERE id=?", (1 if value else 0, article_id))
        row = conn.execute("SELECT title, source FROM articles WHERE id=?", (article_id,)).fetchone()
        if row and value:
            conn.execute(
                """INSERT OR IGNORE INTO irrelevant_examples (title, source, marked_at)
                   VALUES (?, ?, ?)""",
                (row["title"], row["source"], now),
            )
        elif row:
            # 同標題可能有多篇文章，仍有其他篇被標記就不該從池子移除
            still_marked = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE title=? AND irrelevant=1",
                (row["title"],),
            ).fetchone()[0]
            if still_marked == 0:
                conn.execute("DELETE FROM irrelevant_examples WHERE title=?", (row["title"],))
        conn.commit()


def get_articles(source=None, date=None, score_min=None, score_max=None, query=None, page=1, show_irrelevant=False, db_path=DB_PATH):
    """Return paginated articles for dashboard."""
    conditions = []
    params = []
    if not show_irrelevant:
        conditions.append("irrelevant = 0 AND auto_irrelevant = 0")
    else:
        conditions.append("(irrelevant = 1 OR auto_irrelevant = 1)")
    if source and source != "all":
        conditions.append("source = ?")
        params.append(source)
    if date:
        conditions.append("DATE(fetched_at) = ?")
        params.append(date)
    if score_min is not None:
        conditions.append("score >= ?")
        params.append(score_min)
    if score_max is not None:
        conditions.append("score <= ?")
        params.append(score_max)
    if query:
        conditions.append("(title LIKE ? OR summary LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    limit = 20
    offset = (page - 1) * limit

    with get_conn(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM articles {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM articles {where} ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "articles": [dict(r) for r in rows]}


def get_trend_data(period="day", db_path=DB_PATH):
    """Return average scores per time bucket per source, for Chart.js.
    Labels are always filled to cover the full period (nulls for empty buckets)."""
    from datetime import datetime, timedelta, timezone

    now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
    today_tw = now_tw.date()

    if period == "day":
        time_bucket = "strftime('%H:00', datetime(fetched_at, '+8 hours'))"
        condition = "DATE(datetime(fetched_at, '+8 hours')) = ?", [str(today_tw)]
        all_labels = [f"{h:02d}:00" for h in range(now_tw.hour + 1)]
    elif period == "week":
        time_bucket = "strftime('%m-%d', datetime(fetched_at, '+8 hours'))"
        since = str(today_tw - timedelta(days=6))
        condition = "DATE(datetime(fetched_at, '+8 hours')) >= ?", [since]
        all_labels = [(today_tw - timedelta(days=i)).strftime("%m-%d") for i in range(6, -1, -1)]
    else:  # month
        time_bucket = "strftime('%m-%d', datetime(fetched_at, '+8 hours'))"
        since = str(today_tw - timedelta(days=29))
        condition = "DATE(datetime(fetched_at, '+8 hours')) >= ?", [since]
        all_labels = [(today_tw - timedelta(days=i)).strftime("%m-%d") for i in range(29, -1, -1)]

    cond_sql, cond_params = condition

    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT {time_bucket} as t, source, ROUND(AVG(score), 2) as avg_score
                FROM articles
                WHERE score IS NOT NULL AND irrelevant = 0 AND auto_irrelevant = 0 AND {cond_sql}
                GROUP BY t, source
                ORDER BY t""",
            cond_params,
        ).fetchall()

    return [dict(r) for r in rows], all_labels


def get_bullish_trend_data(period="day", db_path=DB_PATH):
    """Return 多方(score>=7)佔比 per time bucket，供 Chart.js 畫多方百分比時間線。
    分桶邏輯與 get_trend_data 相同（day=每小時、week/month=每日），空桶補 null。"""
    from datetime import datetime, timedelta, timezone

    now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
    today_tw = now_tw.date()

    if period == "day":
        time_bucket = "strftime('%H:00', datetime(fetched_at, '+8 hours'))"
        condition = "DATE(datetime(fetched_at, '+8 hours')) = ?", [str(today_tw)]
        all_labels = [f"{h:02d}:00" for h in range(now_tw.hour + 1)]
    elif period == "week":
        time_bucket = "strftime('%m-%d', datetime(fetched_at, '+8 hours'))"
        since = str(today_tw - timedelta(days=6))
        condition = "DATE(datetime(fetched_at, '+8 hours')) >= ?", [since]
        all_labels = [(today_tw - timedelta(days=i)).strftime("%m-%d") for i in range(6, -1, -1)]
    else:  # month
        time_bucket = "strftime('%m-%d', datetime(fetched_at, '+8 hours'))"
        since = str(today_tw - timedelta(days=29))
        condition = "DATE(datetime(fetched_at, '+8 hours')) >= ?", [since]
        all_labels = [(today_tw - timedelta(days=i)).strftime("%m-%d") for i in range(29, -1, -1)]

    cond_sql, cond_params = condition

    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT {time_bucket} as t,
                       ROUND(100.0 * SUM(CASE WHEN score >= 7 THEN 1 ELSE 0 END) / COUNT(*), 1) as bullish_pct
                FROM articles
                WHERE score IS NOT NULL AND irrelevant = 0 AND auto_irrelevant = 0 AND {cond_sql}
                GROUP BY t
                ORDER BY t""",
            cond_params,
        ).fetchall()

    pct_map = {r["t"]: r["bullish_pct"] for r in rows}
    return [pct_map.get(t) for t in all_labels], all_labels
