import telegram_bot as tb
import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def test_unauthorized_chat_is_ignored(tmp_path):
    conn = _conn(tmp_path)
    assert tb.handle("/ls", conn, "999") == ""


def test_help_lists_commands(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/help", conn, tb.CHAT_ID)
    assert "/plan" in out and "/add" in out


def test_add_requires_double_colon(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/add /tmp 沒有分隔", conn, tb.CHAT_ID)
    assert "::" in out          # 回覆要教他正確格式
    assert tq.list_tasks(conn) == []


def test_add_creates_task_with_cwd(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    out = tb.handle(f"/add {work} :: 做點事", conn, tb.CHAT_ID)
    tasks = tq.list_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0]["cwd"] == str(work)
    assert tasks[0]["spec"] == "做點事"
    assert tasks[0]["id"] in out


def test_plan_creates_orchestrator_task(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    tb.handle(f"/plan {work} :: 研究並報告", conn, tb.CHAT_ID)
    tasks = tq.list_tasks(conn)
    assert tasks[0]["kind"] == "orchestrator"


def test_add_rejects_missing_cwd(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/add /no/such/dir :: x", conn, tb.CHAT_ID)
    assert "目錄" in out
    assert tq.list_tasks(conn) == []


def test_ls_lists_tasks(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    tb.handle(f"/add {work} :: 甲", conn, tb.CHAT_ID)
    assert "甲" in tb.handle("/ls", conn, tb.CHAT_ID)


def test_confirm_releases_children(tmp_path):
    conn = _conn(tmp_path)
    pid = tq.add_task(conn, "母", "x", kind="orchestrator")
    c1 = tq.add_task(conn, "子", "s")
    tq.set_status(conn, c1, "blocked", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    out = tb.handle(f"/confirm {pid}", conn, tb.CHAT_ID)
    assert "1" in out
    assert tq.get_task(conn, c1)["status"] == "pending"


def test_unknown_command_returns_hint(tmp_path):
    conn = _conn(tmp_path)
    assert "指令" in tb.handle("/bogus", conn, tb.CHAT_ID)
