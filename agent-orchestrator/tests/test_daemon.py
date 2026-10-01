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
