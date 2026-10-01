import stat

import daemon
import taskqueue as tq


def test_full_flow_add_run_inspect(tmp_path):
    """丟任務 → daemon 跑 → 狀態與產出可查。"""
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)

    fake = tmp_path / "fake_claude"
    fake.write_text('#!/bin/bash\necho "任務完成：$(pwd)"\nexit 0\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    workdir = tmp_path / "work"
    workdir.mkdir()
    tid = tq.add_task(conn, "端到端", "做一件事", cwd=str(workdir))

    assert daemon.tick(conn, claude_bin=str(fake), timeout=10) == 1

    got = tq.get_task(conn, tid)
    assert got["status"] == "done"
    assert "任務完成" in got["result"]
    assert str(workdir) in got["result"]  # 證實 claude 在對的 cwd 執行
