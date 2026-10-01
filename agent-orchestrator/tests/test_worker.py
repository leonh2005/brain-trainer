import stat

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
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "boom" >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
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
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'sleep 30\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"] or "timeout" in got["error"]
