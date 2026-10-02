import stat
import time

import daemon
import orchestrator
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


def test_orchestrator_end_to_end(tmp_path):
    """plan → 拆解 → confirm → 子任務執行 → 自動彙整。"""
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)

    children_json = (
        '{"subtasks": ['
        '{"title": "甲", "spec": "做甲", "cwd": "%s", "depends_on": []},'
        '{"title": "乙", "spec": "做乙", "cwd": "%s", "depends_on": [0]}'
        ']}' % (tmp_path, tmp_path)
    )
    # 比對字串要挑 PLAN_PROMPT / SUMMARIZE_PROMPT 各自獨有、且不會被
    # 任務描述本身污染的字（彙整的 prompt 會內嵌原始任務描述）
    fake = tmp_path / "fake_claude"
    fake.write_text(
        "#!/bin/bash\n"
        "case \"$2\" in\n"
        f"  *任務規劃器*) echo '{children_json}' ;;\n"
        "  *簡短總結*) echo '總結完成' ;;\n"
        "  *) echo '子任務完成' ;;\n"
        "esac\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    pid = tq.add_task(conn, "大任務", "把甲和乙兩件事做出來",
                      cwd=str(tmp_path), kind="orchestrator")
    daemon.tick(conn, claude_bin=str(fake), timeout=10)  # 拆解
    assert tq.get_task(conn, pid)["status"] == "blocked"
    orchestrator.confirm(conn, pid)  # 放行
    for _ in range(4):  # 跑子任務直到沒有 pending，最後觸發彙整
        daemon.tick(conn, claude_bin=str(fake), timeout=10)
    assert tq.get_task(conn, pid)["status"] == "done"
    assert "總結" in tq.get_task(conn, pid)["result"]


def test_parallel_run_finishes_all_tasks(tmp_path):
    """多個獨立任務並行跑完，且每個只跑一次。"""
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    db = str(tmp_path / "t.db")
    ran = tmp_path / "ran"
    fake = tmp_path / "fake_claude"
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "$2" >> {ran}\n'
        "sleep 1\n"
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    for i in range(3):
        tq.add_task(conn, f"t{i}", f"job-{i}", cwd=str(tmp_path))
    daemon.start_ready(conn, db, claude_bin=str(fake), timeout=30, parallel=3)
    for _ in range(60):  # 等背景 thread 收工
        if all(t["status"] == "done" for t in tq.list_tasks(conn)):
            break
        time.sleep(0.5)
    tasks = tq.list_tasks(conn)
    assert all(t["status"] == "done" for t in tasks)
    lines = ran.read_text().splitlines()
    assert sorted(lines) == ["job-0", "job-1", "job-2"]  # 每個只跑一次
