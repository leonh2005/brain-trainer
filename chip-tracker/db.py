# -*- coding: utf-8 -*-
"""chip-tracker SQLite 存取層。"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chip.db")

_DDL = """
CREATE TABLE IF NOT EXISTS lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS stocks (
    code      TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    list_id   INTEGER NOT NULL REFERENCES lists(id),
    added_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS daily (
    code           TEXT NOT NULL,
    date           TEXT NOT NULL,
    foreign_net    INTEGER,
    trust_net      INTEGER,
    dealer_net     INTEGER,
    total_net      INTEGER,
    margin_balance INTEGER,
    short_balance  INTEGER,
    close          REAL,
    change_pct     REAL,
    change_point   REAL,
    foreign_ratio  REAL,
    UNIQUE(code, date)
);
CREATE TABLE IF NOT EXISTS weekly_tdcc (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    big400_pct    REAL,
    whale_holders INTEGER,
    retail_pct    REAL,
    UNIQUE(code, date)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_DAILY_COLS = (
    "foreign_net", "trust_net", "dealer_net", "total_net",
    "margin_balance", "short_balance", "close", "change_pct", "change_point",
    "foreign_ratio", "open", "high", "low", "volume",
)


def get_conn(db_path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout：web 同步回補與排程 updater 可能同時寫入
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_DDL)
    # 既有 DB 遷移：舊表補 foreign_ratio 欄（新表已含於 DDL）
    try:
        conn.execute("ALTER TABLE daily ADD COLUMN foreign_ratio REAL")
    except sqlite3.OperationalError:
        pass  # duplicate column
    try:
        conn.execute("ALTER TABLE daily ADD COLUMN change_point REAL")
    except sqlite3.OperationalError:
        pass  # duplicate column
    for col, typ in (("open", "REAL"), ("high", "REAL"), ("low", "REAL"), ("volume", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE daily ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # duplicate column
    _migrate_lists(conn)
    return conn


def _migrate_lists(conn) -> None:
    """自訂多清單遷移：seed 預設清單 + 把舊版 stocks.list_type 換成 list_id（一次性，idempotent）。

    新建 DB 的 stocks 表由 _DDL 直接建成 list_id 版本，這裡只處理帶著舊
    list_type CHECK 欄位的既有 DB。
    """
    if conn.execute("SELECT COUNT(*) AS n FROM lists").fetchone()["n"] == 0:
        conn.execute("INSERT INTO lists (name, sort_order) VALUES ('庫存', 0), ('觀察', 1)")
        conn.commit()

    cols = [r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    if "list_type" not in cols:
        return  # 新 schema 或已遷移過

    holding_id = conn.execute("SELECT id FROM lists WHERE name='庫存'").fetchone()["id"]
    watch_id = conn.execute("SELECT id FROM lists WHERE name='觀察'").fetchone()["id"]
    conn.execute("ALTER TABLE stocks RENAME TO stocks_old")
    conn.execute("""
        CREATE TABLE stocks (
            code      TEXT PRIMARY KEY,
            name      TEXT NOT NULL DEFAULT '',
            list_id   INTEGER NOT NULL REFERENCES lists(id),
            added_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute(
        "INSERT INTO stocks (code, name, list_id, added_at) "
        "SELECT code, name, CASE list_type WHEN 'holding' THEN ? ELSE ? END, added_at FROM stocks_old",
        (holding_id, watch_id),
    )
    conn.execute("DROP TABLE stocks_old")
    conn.commit()


def list_stocks(conn) -> list:
    rows = conn.execute(
        "SELECT code, name, list_id FROM stocks ORDER BY added_at"
    ).fetchall()
    return [dict(r) for r in rows]


def add_stock(conn, code: str, name: str, list_id: int) -> None:
    conn.execute(
        "INSERT INTO stocks (code, name, list_id) VALUES (?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET name=excluded.name, list_id=excluded.list_id",
        (code, name, list_id),
    )
    conn.commit()


def list_lists(conn) -> list:
    rows = conn.execute(
        "SELECT id, name, sort_order FROM lists ORDER BY sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def add_list(conn, name: str) -> int:
    try:
        cur = conn.execute("INSERT INTO lists (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        raise ValueError(f"清單「{name}」已存在")
    conn.commit()
    return cur.lastrowid


def delete_list(conn, list_id: int) -> None:
    row = conn.execute("SELECT name FROM lists WHERE id = ?", (list_id,)).fetchone()
    if not row:
        raise ValueError("清單不存在")
    if row["name"] in ("庫存", "觀察"):
        raise ValueError("預設清單無法刪除")
    count = conn.execute("SELECT COUNT(*) AS n FROM stocks WHERE list_id = ?", (list_id,)).fetchone()["n"]
    if count > 0:
        raise ValueError("清單內尚有股票，請先移除")
    conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
    conn.commit()


def remove_stock(conn, code: str) -> None:
    conn.execute("DELETE FROM stocks WHERE code = ?", (code,))
    conn.commit()


def upsert_daily(conn, code: str, date: str, **cols) -> None:
    cols = {k: v for k, v in cols.items() if k in _DAILY_COLS}
    if not cols:
        return
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k}=COALESCE(excluded.{k}, {k})" for k in cols)
    conn.execute(
        f"INSERT INTO daily (code, date, {names}) VALUES (?, ?, {placeholders}) "
        f"ON CONFLICT(code, date) DO UPDATE SET {updates}",
        (code, date, *cols.values()),
    )


def upsert_weekly(conn, code: str, date: str, big400_pct, whale_holders, retail_pct) -> None:
    conn.execute(
        "INSERT INTO weekly_tdcc (code, date, big400_pct, whale_holders, retail_pct) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(code, date) DO UPDATE SET "
        "big400_pct=COALESCE(excluded.big400_pct, big400_pct), "
        "whale_holders=COALESCE(excluded.whale_holders, whale_holders), "
        "retail_pct=COALESCE(excluded.retail_pct, retail_pct)",
        (code, date, big400_pct, whale_holders, retail_pct),
    )


def get_daily_history(conn, code: str, days: int = 30) -> list:
    rows = conn.execute(
        "SELECT * FROM daily WHERE code = ? ORDER BY date DESC LIMIT ?",
        (code, days),
    ).fetchall()
    return [dict(r) for r in rows]


def get_weekly_history(conn, code: str, weeks: int = 12) -> list:
    rows = conn.execute(
        "SELECT * FROM weekly_tdcc WHERE code = ? ORDER BY date DESC LIMIT ?",
        (code, weeks),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_daily_date(conn, code: str):
    row = conn.execute(
        "SELECT MAX(date) AS d FROM daily WHERE code = ?", (code,)
    ).fetchone()
    return row["d"]


def latest_weekly_date(conn):
    row = conn.execute("SELECT MAX(date) AS d FROM weekly_tdcc").fetchone()
    return row["d"]


def get_meta(conn, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def streak(values: list) -> int:
    """連續買超天數：從最新往回數連續 > 0 的天數（values 依日期新到舊）。"""
    count = 0
    for v in values:
        if v is None or v <= 0:
            break
        count += 1
    return count


def price_streak(values: list) -> int:
    """連漲跌天數：正數＝連漲天數，負數＝連跌天數，0＝平盤或無資料（values 依日期新到舊）。"""
    if not values or values[0] is None or values[0] == 0:
        return 0
    up = values[0] > 0
    count = 0
    for v in values:
        if v is None or v == 0 or (v > 0) != up:
            break
        count += 1
    return count if up else -count


def price_streak_pct(values: list, streak: int) -> float | None:
    """連漲跌期間累計漲跌幅（複利），streak 為 price_streak() 的結果。"""
    n = abs(streak)
    if n == 0:
        return None
    cum = 1.0
    for v in values[:n]:
        cum *= 1 + v / 100
    return round((cum - 1) * 100, 2)
