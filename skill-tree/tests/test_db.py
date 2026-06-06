import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import db

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()

def test_calc_level_starts_at_1():
    level, current, to_next = db.calc_level(0)
    assert level == 1
    assert current == 0
    assert to_next == 100

def test_calc_level_lv2_at_100xp():
    level, _, _ = db.calc_level(100)
    assert level == 2

def test_calc_level_max_20():
    level, _, _ = db.calc_level(999999)
    assert level == 20

def test_xp_accumulates():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
    db.log_xp(1, 50, "a")
    db.log_xp(1, 70, "b")
    assert db.get_skill_xp(1) == 120

def test_skill_detail_returns_level():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
    db.log_xp(1, 100, "got lv2")
    detail = db.get_skill_detail(1)
    assert detail['level'] == 2

def test_get_character_level_sum():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S2')")
    db.log_xp(1, 100, "")  # skill 1 → lv2
    char = db.get_character()
    assert char['total_level'] == 3  # skill1=lv2, skill2=lv1 → 2+1=3
