"""所有 SQLite 讀寫的唯一入口。"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    capital_twd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    market TEXT NOT NULL,
    shares REAL NOT NULL,
    price_native REAL NOT NULL,
    fx_rate REAL NOT NULL,
    cost_twd REAL NOT NULL,
    tranche_no INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots(
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    total_value_twd REAL NOT NULL,
    cash_twd REAL NOT NULL,
    unrealized_pnl_twd REAL NOT NULL,
    by_category_json TEXT NOT NULL,
    by_ticker_json TEXT,
    PRIMARY KEY(account_id, date)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(snapshots)")}
    if "by_ticker_json" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN by_ticker_json TEXT")
        conn.commit()
    return conn


def create_account(conn, account_id, name, plan_id, start_date, capital_twd):
    conn.execute(
        "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?)",
        (account_id, name, plan_id, start_date, capital_twd),
    )
    conn.commit()


def get_account(conn, account_id):
    return conn.execute(
        "SELECT * FROM accounts WHERE account_id=?", (account_id,)
    ).fetchone()


def add_trade(conn, account_id, date, ticker, market, shares,
              price_native, fx_rate, cost_twd, tranche_no):
    conn.execute(
        "INSERT INTO trades(account_id,date,ticker,market,shares,price_native,"
        "fx_rate,cost_twd,tranche_no) VALUES(?,?,?,?,?,?,?,?,?)",
        (account_id, date, ticker, market, shares, price_native,
         fx_rate, cost_twd, tranche_no),
    )
    conn.commit()


def get_trades(conn, account_id):
    return conn.execute(
        "SELECT * FROM trades WHERE account_id=? ORDER BY id", (account_id,)
    ).fetchall()


def add_snapshot(conn, account_id, date, total_value_twd, cash_twd,
                 unrealized_pnl_twd, by_category_json, by_ticker_json=None):
    conn.execute(
        "INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?)",
        (account_id, date, total_value_twd, cash_twd,
         unrealized_pnl_twd, by_category_json, by_ticker_json),
    )
    conn.commit()


def get_snapshots(conn, account_id):
    return conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? ORDER BY date", (account_id,)
    ).fetchall()


def latest_snapshot(conn, account_id):
    return conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? ORDER BY date DESC LIMIT 1",
        (account_id,),
    ).fetchone()
