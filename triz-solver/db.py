# -*- coding: utf-8 -*-
"""triz-solver SQLite 歷史紀錄存取層。"""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triz.db")

_DDL = """
CREATE TABLE IF NOT EXISTS history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    problem      TEXT NOT NULL DEFAULT '',
    improving    INTEGER,
    worsening    INTEGER,
    principle_ids TEXT NOT NULL DEFAULT '[]',
    suggestions  TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    return conn


def add_history(problem: str, improving: int, worsening: int, principle_ids: list, suggestions: list) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO history (problem, improving, worsening, principle_ids, suggestions) "
        "VALUES (?, ?, ?, ?, ?)",
        (problem, improving, worsening, json.dumps(principle_ids), json.dumps(suggestions, ensure_ascii=False)),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_history(limit: int = 100) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["principle_ids"] = json.loads(d["principle_ids"])
        d["suggestions"] = json.loads(d["suggestions"])
        out.append(d)
    return out
