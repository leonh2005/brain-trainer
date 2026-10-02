import json
import stat
import time

import daemon
import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def _fake_claude(tmp_path, body):
    path = tmp_path / "fake_claude"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_reap_timed_out_marks_stale_running(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    # 模擬：daemon 上次執行到一半就掛了，留下 running 且 started_at 很久以前
    tq.set_status(conn, tid, "running", started_at=time.time() - 99999)
    n = daemon.reap_timed_out(conn, timeout=10)
    assert n == 1
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"]


def test_reap_leaves_fresh_running_alone(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running", started_at=time.time())
    assert daemon.reap_timed_out(conn, timeout=3600) == 0
    assert tq.get_task(conn, tid)["status"] == "running"


def test_tick_runs_one_task(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 1
    assert tq.get_task(conn, tid)["status"] == "done"


def test_tick_returns_zero_when_nothing_ready(tmp_path):
    conn = _conn(tmp_path)
    assert daemon.tick(conn, claude_bin="claude", timeout=10) == 0


def test_tick_does_not_rerun_done_task(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    daemon.tick(conn, claude_bin=fake, timeout=10)
    # 再跑一輪不應重跑已完成的任務
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 0
    assert tq.get_task(conn, tid)["status"] == "done"


def test_safe_tick_swallows_exception(tmp_path, monkeypatch):
    """常駐迴圈不能因為一輪的意外例外就整個死掉。"""
    conn = _conn(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(daemon, "tick", boom)
    assert daemon._tick_safe(conn) == 0  # 不該把例外穿出去


def test_tick_decomposes_orchestrator_task(tmp_path):
    conn = _conn(tmp_path)
    body = ('echo \'{"subtasks": [{"title": "a", "spec": "sa", '
            '"cwd": "%s", "depends_on": []}]}\'\n' % tmp_path)
    fake = _fake_claude(tmp_path, body)
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 1
    assert tq.get_task(conn, pid)["status"] == "blocked"


def test_tick_summarizes_when_children_done(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '彙整完成'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="子一完成", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    daemon.tick(conn, claude_bin=fake, timeout=10)  # 沒有 pending 任務，但要觸發彙整
    assert tq.get_task(conn, pid)["status"] == "done"


def test_parent_fails_when_child_fails_with_dependent_pending(tmp_path):
    """子任務失敗、有依賴它的手足還在等 → 母任務要收尾成 failed，不能永遠卡 blocked。"""
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '不該被呼叫'\n")
    pid = tq.add_task(conn, "母", "x", cwd=str(tmp_path), kind="orchestrator")
    a = tq.add_task(conn, "甲", "sa", cwd=str(tmp_path))
    b = tq.add_task(conn, "乙", "sb", cwd=str(tmp_path))
    tq.set_status(conn, a, "failed", error="甲壞了", parent_id=pid)
    tq.set_status(conn, b, "pending", parent_id=pid, depends_on=json.dumps([a]))
    tq.set_status(conn, pid, "blocked")
    daemon.tick(conn, claude_bin=fake, timeout=10)
    assert tq.get_task(conn, pid)["status"] == "failed"
    assert tq.get_task(conn, b)["status"] == "failed"  # 孤兒被標掉，不留在 pending
