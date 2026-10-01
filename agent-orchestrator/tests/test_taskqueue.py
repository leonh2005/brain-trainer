import pytest

import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def test_init_creates_tasks_table(tmp_path):
    conn = _conn(tmp_path)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "tasks" in names


def test_connection_enables_wal_and_busy_timeout(tmp_path):
    # 讓多個行程同時碰 DB 時不會立刻 database is locked
    conn = _conn(tmp_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_add_and_get_task(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "標題", "做某事", cwd="/tmp")
    t = tq.get_task(conn, tid)
    assert t["title"] == "標題"
    assert t["spec"] == "做某事"
    assert t["status"] == "pending"
    assert t["depends_on"] == []
    assert t["retries"] == 0


def test_get_missing_returns_none(tmp_path):
    conn = _conn(tmp_path)
    assert tq.get_task(conn, "nope") is None


def test_list_tasks_filters_by_status(tmp_path):
    conn = _conn(tmp_path)
    a = tq.add_task(conn, "a", "sa")
    tq.add_task(conn, "b", "sb")
    tq.set_status(conn, a, "done")
    assert [t["id"] for t in tq.list_tasks(conn, status="done")] == [a]
    assert len(tq.list_tasks(conn)) == 2


def test_set_status_rejects_invalid(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "x", "y")
    with pytest.raises(ValueError):
        tq.set_status(conn, tid, "bogus")


def test_claim_next_respects_dependencies(tmp_path):
    conn = _conn(tmp_path)
    a = tq.add_task(conn, "A", "sa")
    b = tq.add_task(conn, "B", "sb", depends_on=[a])
    # A 沒依賴 → 先被撿到
    assert tq.claim_next(conn)["id"] == a
    # A 現在是 running（尚未 done）→ B 還不能跑
    assert tq.claim_next(conn) is None
    tq.set_status(conn, a, "done")
    assert tq.claim_next(conn)["id"] == b


def test_claim_next_marks_running_atomically(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "A", "sa")
    tq.claim_next(conn)
    t = tq.get_task(conn, tid)
    assert t["status"] == "running"
    assert t["started_at"] is not None
