import sqlite3
import json
from datetime import datetime, timezone

DB_PATH = "news.db"


def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path=DB_PATH):
    with get_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source       TEXT NOT NULL,
                title        TEXT NOT NULL,
                url          TEXT UNIQUE NOT NULL,
                content      TEXT,
                published_at DATETIME,
                fetched_at   DATETIME NOT NULL,
                score        INTEGER,
                summary      TEXT,
                tags         TEXT,
                analyzed_at  DATETIME
            )
        """)
        conn.commit()


def save_articles(articles, db_path=DB_PATH):
    """Insert articles, ignore duplicates by URL."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
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


def get_unanalyzed(db_path=DB_PATH):
    """Return up to 100 articles with no score yet."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, source, title, content FROM articles WHERE score IS NULL ORDER BY fetched_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]


def update_analysis(article_id, score, summary, tags, db_path=DB_PATH):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE articles SET score=?, summary=?, tags=?, analyzed_at=? WHERE id=?",
            (score, summary, json.dumps(tags, ensure_ascii=False), now, article_id),
        )
        conn.commit()


def get_articles(source=None, date=None, score_min=None, score_max=None, query=None, page=1, db_path=DB_PATH):
    """Return paginated articles for dashboard."""
    conditions = []
    params = []
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
            f"SELECT * FROM articles {where} ORDER BY fetched_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "articles": [dict(r) for r in rows]}


def get_trend_data(period="day", db_path=DB_PATH):
    """Return average scores per time bucket per source, for Chart.js."""
    if period == "day":
        time_bucket = "strftime('%H:00', fetched_at)"
        condition = "DATE(fetched_at) = DATE('now')"
    elif period == "week":
        time_bucket = "strftime('%m-%d', fetched_at)"
        condition = "fetched_at >= DATE('now', '-7 days')"
    else:  # month
        time_bucket = "strftime('%m-%d', fetched_at)"
        condition = "fetched_at >= DATE('now', '-30 days')"

    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT {time_bucket} as t, source, ROUND(AVG(score), 2) as avg_score
                FROM articles
                WHERE score IS NOT NULL AND {condition}
                GROUP BY t, source
                ORDER BY t""",
        ).fetchall()
        return [dict(r) for r in rows]
