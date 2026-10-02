import stat
import time

import taskqueue as tq
import worker


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def _fake_claude(tmp_path, body):
    """做一個假的 claude 執行檔，內容為 body。"""
    path = tmp_path / "fake_claude"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_run_task_success_writes_result(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "done-ok"\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec here", cwd=str(tmp_path))
    ok, out = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "done"
    assert "done-ok" in got["result"]


def test_run_task_nonzero_exit_marks_failed(tmp_path):
    # 非 0 結束現在會先退回 pending 重試；已達上限才 failed
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "boom" >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "boom" in got["error"]


def test_run_task_missing_cwd_fails_cleanly(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'exit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd="/nonexistent_dir_xyz")
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    assert "工作目錄" in tq.get_task(conn, tid)["error"]


def test_run_task_missing_binary_fails_cleanly(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid),
                           claude_bin="/no/such/claude", timeout=10)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert got["error"]


def test_run_task_passes_spec_verbatim(tmp_path):
    """spec 含引號、$、換行 → 必須原樣到 claude 手上（不經 shell）。"""
    conn = _conn(tmp_path)
    out_file = tmp_path / "argv.txt"
    fake = _fake_claude(tmp_path, f'printf "%s" "$2" > {out_file}\n')
    weird = 'has "quotes" and $HOME and\nnewline'
    tid = tq.add_task(conn, "t", weird, cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert ok, "假 claude 應正常結束"
    assert out_file.read_text() == weird


def test_run_task_timeout_marks_failed(tmp_path):
    # 逾時現在會先退回 pending 重試；已達上限才 failed
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'sleep 30\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"] or "timeout" in got["error"]


def test_run_task_rejects_missing_cwd(tmp_path):
    """沒指定 cwd 的任務不能默默在 daemon 所在目錄跑。"""
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'exit 0\n')
    tid = tq.add_task(conn, "t", "spec")  # 不給 cwd
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    assert tq.get_task(conn, tid)["status"] == "failed"


def test_run_task_permission_denied_fails_cleanly(tmp_path):
    """執行檔存在但不可執行 → 收斂成 failed，不要讓例外穿出去打死 daemon。"""
    conn = _conn(tmp_path)
    noexec = tmp_path / "noexec"
    noexec.write_text("#!/bin/bash\necho hi\n")
    noexec.chmod(0o644)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=str(noexec), timeout=10)
    assert not ok
    assert tq.get_task(conn, tid)["status"] == "failed"


def test_run_task_timeout_kills_children(tmp_path):
    """逾時要殺掉整個 process group，孫行程不能活下來繼續跑。"""
    conn = _conn(tmp_path)
    marker = tmp_path / "grandchild.txt"
    fake = _fake_claude(tmp_path, f'(sleep 3; echo alive > {marker}) &\nsleep 30\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=2)
    assert not ok
    time.sleep(4)  # 超過孫行程原本要寫檔的時間
    assert not marker.exists(), "孫行程應已被殺，不該寫出檔案"


def test_run_task_records_worker_pid(tmp_path):
    # 成功完成時 worker_pid 會保留；逾時退回重試時會清掉（見 _fail）
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, tid)["worker_pid"] is not None


def test_run_task_retries_on_nonzero_exit(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo boom >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, tid)
    assert got["status"] == "pending"          # 退回佇列等重試
    assert got["retries"] == 1
    assert got["next_attempt_at"] > time.time()


def test_run_task_gives_up_after_max_retries(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo boom >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "boom" in got["error"]


def test_run_task_does_not_retry_missing_cwd(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "exit 0\n")
    tid = tq.add_task(conn, "t", "s", cwd="/nonexistent_dir_xyz")
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, tid)["status"] == "failed"   # 環境問題不重試


def test_run_task_retries_on_timeout(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "sleep 30\n")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert tq.get_task(conn, tid)["status"] == "pending"
