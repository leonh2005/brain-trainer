import json
import os
import stat
import time

import pytest

import daemon
import taskqueue as tq
import worker as w


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


def test_finalize_summarizes_when_children_done(tmp_path):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, "echo '彙整完成'\n")  # 彙整走 text 模式（無 idle_timeout）
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="子一完成", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    daemon.finalize_parents(conn, db, claude_bin=fake, timeout=10)
    for _ in range(40):  # 彙整在背景跑
        if tq.get_task(conn, pid)["status"] == "done":
            break
        time.sleep(0.5)
    assert tq.get_task(conn, pid)["status"] == "done"


def test_parent_fails_when_child_fails_with_dependent_pending(tmp_path):
    """子任務失敗、有依賴它的手足還在等 → 母任務要收尾成 failed，不能永遠卡 blocked。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, "echo '不該被呼叫'\n")
    pid = tq.add_task(conn, "母", "x", cwd=str(tmp_path), kind="orchestrator")
    a = tq.add_task(conn, "甲", "sa", cwd=str(tmp_path))
    b = tq.add_task(conn, "乙", "sb", cwd=str(tmp_path))
    tq.set_status(conn, a, "failed", error="甲壞了", parent_id=pid)
    tq.set_status(conn, b, "pending", parent_id=pid, depends_on=json.dumps([a]))
    tq.set_status(conn, pid, "blocked")
    daemon.finalize_parents(conn, db, claude_bin=fake, timeout=10)
    for _ in range(40):
        if tq.get_task(conn, pid)["status"] == "failed":
            break
        time.sleep(0.5)
    assert tq.get_task(conn, pid)["status"] == "failed"
    assert tq.get_task(conn, b)["status"] == "failed"  # 孤兒被標掉，不留在 pending


def test_start_ready_respects_parallel_limit(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: None)
    for i in range(5):
        tq.add_task(conn, f"t{i}", "s", cwd=str(tmp_path))
    started = daemon.start_ready(conn, db, parallel=2)
    assert len(started) == 2
    assert len(tq.list_tasks(conn, status="running")) == 2


def test_start_ready_counts_existing_running(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: None)
    a = tq.add_task(conn, "a", "s", cwd=str(tmp_path))
    tq.set_status(conn, a, "running")  # 已經有一個在跑
    tq.add_task(conn, "b", "s", cwd=str(tmp_path))
    started = daemon.start_ready(conn, db, parallel=2)
    assert len(started) == 1  # 只剩一個名額


def test_start_ready_returns_empty_when_full(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: None)
    a = tq.add_task(conn, "a", "s", cwd=str(tmp_path))
    tq.set_status(conn, a, "running")
    assert daemon.start_ready(conn, str(tmp_path / "t.db"), parallel=1) == []


def test_worker_thread_marks_task_done(tmp_path):
    """背景執行真的會把任務跑完（不是只有啟動）。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    # _run_worker 走 stream-json 路徑，假 claude 要吐 result 事件
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)
    assert tq.get_task(conn, tid)["status"] == "done"


def test_run_worker_uses_idle_timeout(tmp_path):
    """並行路徑要啟用閒置偵測（卡住的任務會被殺，不是等滿總時長）。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, "sleep 30\n")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    original = w.IDLE_TIMEOUT
    w.IDLE_TIMEOUT = 2  # 縮短以利測試
    try:
        daemon._run_worker(db, tid, fake, 60)
    finally:
        w.IDLE_TIMEOUT = original
    got = tq.get_task(conn, tid)
    assert got["error"] and "閒置" in got["error"]


def test_finalize_parents_does_not_block(tmp_path):
    """彙整要在背景跑，不能卡住主迴圈（否則並行形同關閉）。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, "sleep 2\necho '總結完成'\n")
    pid = tq.add_task(conn, "母", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子", "s", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="完成", parent_id=pid)
    tq.set_status(conn, pid, "blocked")

    t0 = time.time()
    daemon.finalize_parents(conn, db, claude_bin=str(fake), timeout=30)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"finalize 不該阻塞（花了 {elapsed:.1f}s）"
    for _ in range(40):  # 背景彙整完成
        if tq.get_task(conn, pid)["status"] == "done":
            break
        time.sleep(0.5)
    assert tq.get_task(conn, pid)["status"] == "done"


def test_run_worker_survives_exception(tmp_path, monkeypatch):
    """worker thread 內部例外不能讓任務卡在 running。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")

    def boom(*args, **kwargs):
        raise RuntimeError("模擬 worker 內部錯誤")

    monkeypatch.setattr(w, "run_task", boom)
    daemon._run_worker(db, tid, "claude", 10)  # 不該把例外穿出去
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "worker 例外" in got["error"]


def test_daemon_singleton_rejects_second_instance(tmp_path):
    db = str(tmp_path / "t.db")
    with open(db + ".daemon.lock", "w") as fh:
        fh.write(str(os.getpid()))  # 自己 = 活著
    with pytest.raises(RuntimeError):
        daemon._acquire_singleton(db)


def test_daemon_singleton_replaces_stale_lock(tmp_path):
    db = str(tmp_path / "t.db")
    with open(db + ".daemon.lock", "w") as fh:
        fh.write("999999")  # 幾乎不可能存在的 pid
    daemon._acquire_singleton(db)  # 不該炸
    assert open(db + ".daemon.lock").read().strip() == str(os.getpid())


def test_run_worker_notifies_on_completion(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")
    sent = []
    monkeypatch.setattr(daemon.notify, "send",
                        lambda text, **kw: sent.append(text) or True)
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)
    assert any(tid in s for s in sent)


def test_notify_result_pushes_decomposed_parent(tmp_path, monkeypatch):
    """拆解完成的母任務（blocked）也要推播，否則使用者等不到回報。"""
    conn = _conn(tmp_path)
    sent = []
    monkeypatch.setattr(daemon.notify, "send",
                        lambda text, **kw: sent.append(text) or True)
    pid = tq.add_task(conn, "大任務", "x", kind="orchestrator")
    tq.add_task(conn, "子", "s", status="blocked", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    daemon._notify_result(conn, pid)
    assert any("拆解" in s for s in sent)


def test_notify_result_includes_result_text(tmp_path, monkeypatch):
    """✅ 訊息要帶結果，否則手機上只看得到「完成了」卻看不到做了什麼。"""
    conn = _conn(tmp_path)
    sent = []
    monkeypatch.setattr(daemon.notify, "send",
                        lambda text, **kw: sent.append(text) or True)
    tid = tq.add_task(conn, "t", "s")
    tq.set_status(conn, tid, "done", result="產出：一份報告")
    daemon._notify_result(conn, tid)
    assert any("一份報告" in s for s in sent)


def test_notify_failure_does_not_break_task(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(daemon.notify, "send", boom)
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)  # 不該把例外穿出去
    assert tq.get_task(conn, tid)["status"] == "done"
