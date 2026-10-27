# -*- coding: utf-8 -*-
"""db.py 測試：歷史紀錄的讀寫要用 temp DB，不碰真實 triz.db。"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


@pytest.fixture
def isolated_db(monkeypatch):
    """把 db.DB_PATH 指向 temp 檔，測試完自動清除。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    monkeypatch.setattr(db, "DB_PATH", path)
    yield path
    if os.path.exists(path):
        os.unlink(path)
    # 清掉同目錄的 WAL/SHM（journal_mode=WAL 會產生）
    for ext in ("-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            os.unlink(p)


def test_get_conn_creates_table(isolated_db):
    """get_conn 要建出 history table。"""
    conn = db.get_conn()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    # row_factory 是 sqlite3.Row，不能直接跟 tuple 比，要取 name 欄位
    assert "history" in [r["name"] for r in tables]


def test_add_history_returns_row_id(isolated_db):
    """add_history 要回傳插入的 row id（正整數）。"""
    row_id = db.add_history("測試問題", 1, 3, [15, 8], [{"text": "建議"}])
    assert isinstance(row_id, int)
    assert row_id > 0


def test_add_history_and_list_round_trip(isolated_db):
    """寫入後 list_history 要能完整讀回，含 JSON 欄位解回 list。"""
    principle_ids = [15, 8, 29]
    suggestions = [{"text": "建議一", "image_prompt": "a dog"}]
    db.add_history("矛盾問題", 1, 3, principle_ids, suggestions)

    rows = db.list_history()
    assert len(rows) == 1
    row = rows[0]
    assert row["problem"] == "矛盾問題"
    assert row["improving"] == 1
    assert row["worsening"] == 3
    assert row["principle_ids"] == principle_ids
    assert row["suggestions"] == suggestions


def test_list_history_returns_newest_first(isolated_db):
    """多筆資料要依 id 遞減排序（最新的在前）。"""
    id1 = db.add_history("先", 1, 2, [], [])
    id2 = db.add_history("後", 3, 4, [], [])
    rows = db.list_history()
    assert [r["id"] for r in rows] == [id2, id1]
    assert [r["problem"] for r in rows] == ["後", "先"]


def test_list_history_respects_limit(isolated_db):
    """limit 參數要限制回傳筆數。"""
    for i in range(5):
        db.add_history(f"問題{i}", 1, 2, [], [])
    rows = db.list_history(limit=3)
    assert len(rows) == 3


def test_list_history_empty_db_returns_empty_list(isolated_db):
    """空 DB 要回空 list，不拋例外。"""
    assert db.list_history() == []


def test_add_history_handles_unicode_suggestions(isolated_db):
    """中文/特殊字元要能正確存成 JSON 並讀回（ensure_ascii=False）。"""
    suggestions = [{"text": "加入彈簧緩衝", "image": "彈簧圖"}]
    db.add_history("測試", 1, 2, [], suggestions)
    rows = db.list_history()
    assert rows[0]["suggestions"] == suggestions
