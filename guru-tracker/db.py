# -*- coding: utf-8 -*-
"""guru-tracker SQLite 存取層。"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guru_tracker.db")

_DDL = """
CREATE TABLE IF NOT EXISTS holders (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    name_zh    TEXT NOT NULL,
    type       TEXT NOT NULL CHECK(type IN ('13f','ark','virtual')),
    cik        TEXT,
    source     TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tickers (
    ticker           TEXT PRIMARY KEY,
    name             TEXT,
    sector           TEXT,
    industry         TEXT,
    sector_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS cusip_ticker_map (
    cusip       TEXT PRIMARY KEY,
    ticker      TEXT,
    resolved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings_snapshot (
    holder_id  TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    period     TEXT NOT NULL,
    shares     REAL,
    value_usd  REAL,
    weight_pct REAL,
    filed_date TEXT,
    UNIQUE(holder_id, ticker, period)
);
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
"""


def get_conn(db_path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_DDL)
    return conn


def upsert_holder(conn: sqlite3.Connection, holder_id: str, name: str, name_zh: str,
                   type_: str, cik: str, source: str) -> None:
    conn.execute(
        "INSERT INTO holders (id, name, name_zh, type, cik, source) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, name_zh=excluded.name_zh, "
        "type=excluded.type, cik=excluded.cik, source=excluded.source",
        (holder_id, name, name_zh, type_, cik, source),
    )
    conn.commit()


def list_holders(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM holders ORDER BY rowid").fetchall()
    return [dict(r) for r in rows]


def get_holder(conn: sqlite3.Connection, holder_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM holders WHERE id = ?", (holder_id,)).fetchone()
    return dict(row) if row else None


def touch_holder(conn: sqlite3.Connection, holder_id: str, updated_at: str) -> None:
    conn.execute("UPDATE holders SET updated_at = ? WHERE id = ?", (updated_at, holder_id))
    conn.commit()


def get_cusip_ticker(conn: sqlite3.Connection, cusip: str) -> str | None:
    row = conn.execute("SELECT ticker FROM cusip_ticker_map WHERE cusip = ?", (cusip,)).fetchone()
    return row["ticker"] if row else None


def cusip_already_resolved(conn: sqlite3.Connection, cusip: str) -> bool:
    """區分「已查過（含查無結果）」與「還沒查過」，避免無法解析的 CUSIP 每次重打 OpenFIGI。"""
    row = conn.execute("SELECT 1 FROM cusip_ticker_map WHERE cusip = ?", (cusip,)).fetchone()
    return row is not None


def upsert_cusip_ticker(conn: sqlite3.Connection, cusip: str, ticker: str, resolved_at: str) -> None:
    conn.execute(
        "INSERT INTO cusip_ticker_map (cusip, ticker, resolved_at) VALUES (?, ?, ?) "
        "ON CONFLICT(cusip) DO UPDATE SET ticker=excluded.ticker, resolved_at=excluded.resolved_at",
        (cusip, ticker, resolved_at),
    )


def upsert_ticker_meta(conn: sqlite3.Connection, ticker: str, name: str) -> None:
    conn.execute(
        "INSERT INTO tickers (ticker, name) VALUES (?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET name=COALESCE(excluded.name, name)",
        (ticker, name),
    )


def tickers_missing_sector(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT ticker FROM tickers WHERE sector IS NULL").fetchall()
    return [r["ticker"] for r in rows]


def set_ticker_sector(conn: sqlite3.Connection, ticker: str, sector: str, industry: str, updated_at: str) -> None:
    conn.execute(
        "UPDATE tickers SET sector = ?, industry = ?, sector_updated_at = ? WHERE ticker = ?",
        (sector, industry, updated_at, ticker),
    )


def get_ticker_sector(conn: sqlite3.Connection, ticker: str) -> dict | None:
    row = conn.execute("SELECT sector, industry FROM tickers WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


def upsert_snapshot(conn: sqlite3.Connection, holder_id: str, ticker: str, period: str,
                     shares: float, value_usd: float, weight_pct: float, filed_date: str) -> None:
    conn.execute(
        "INSERT INTO holdings_snapshot (holder_id, ticker, period, shares, value_usd, weight_pct, filed_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(holder_id, ticker, period) DO UPDATE SET "
        "shares=excluded.shares, value_usd=excluded.value_usd, "
        "weight_pct=excluded.weight_pct, filed_date=excluded.filed_date",
        (holder_id, ticker, period, shares, value_usd, weight_pct, filed_date),
    )


def clear_snapshot_period(conn: sqlite3.Connection, holder_id: str, period: str) -> None:
    """重寫某 holder 某期資料前先清空，避免舊持股殘留（該期若已不再持有某檔，需消失而非留著舊值）。"""
    conn.execute("DELETE FROM holdings_snapshot WHERE holder_id = ? AND period = ?", (holder_id, period))


def latest_periods(conn: sqlite3.Connection, holder_id: str, limit: int = 2) -> list:
    rows = conn.execute(
        "SELECT DISTINCT period FROM holdings_snapshot WHERE holder_id = ? ORDER BY period DESC LIMIT ?",
        (holder_id, limit),
    ).fetchall()
    return [r["period"] for r in rows]


def get_snapshot(conn: sqlite3.Connection, holder_id: str, period: str) -> list:
    rows = conn.execute(
        "SELECT * FROM holdings_snapshot WHERE holder_id = ? AND period = ? ORDER BY weight_pct DESC",
        (holder_id, period),
    ).fetchall()
    return [dict(r) for r in rows]


def get_ticker_history(conn: sqlite3.Connection, holder_id: str, ticker: str) -> list:
    """單一標的在該 holder 名下的歷史持倉，依期別由舊到新排序（供走勢圖使用）。"""
    rows = conn.execute(
        "SELECT period, shares, value_usd, weight_pct, filed_date FROM holdings_snapshot "
        "WHERE holder_id = ? AND ticker = ? ORDER BY period ASC",
        (holder_id, ticker),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_snapshot_all_holders(conn: sqlite3.Connection, holder_ids: list) -> dict:
    """回傳 {holder_id: [snapshot rows...]}，各自取自己最新一期。"""
    result = {}
    for hid in holder_ids:
        periods = latest_periods(conn, hid, limit=1)
        result[hid] = get_snapshot(conn, hid, periods[0]) if periods else []
    return result


def get_config(conn: sqlite3.Connection, key: str, default: str = None) -> str:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
