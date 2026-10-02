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


def test_dispatch_ignores_unauthorized(tmp_path):
    conn = _conn(tmp_path)
    upd = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/add /tmp :: x"}}
    assert tb._dispatch(upd, conn) == ""
    assert tq.list_tasks(conn) == []


def test_dispatch_ignores_non_message_updates(tmp_path):
    """channel_post / callback_query / 沒有 message 的 update 都必須忽略。"""
    conn = _conn(tmp_path)
    for upd in ({"update_id": 1},
                {"update_id": 2,
                 "channel_post": {"chat": {"id": int(tb.CHAT_ID)}, "text": "/ls"}},
                {"update_id": 3, "callback_query": {"id": "x"}}):
        assert tb._dispatch(upd, conn) == ""


def test_dispatch_handles_authorized_message(tmp_path):
    conn = _conn(tmp_path)
    upd = {"update_id": 1,
           "message": {"chat": {"id": int(tb.CHAT_ID)}, "text": "/help"}}
    assert "指令" in tb._dispatch(upd, conn)


def test_is_permanent_error():
    import urllib.error
    assert tb._is_permanent(urllib.error.HTTPError("u", 409, "c", {}, None))
    assert tb._is_permanent(urllib.error.HTTPError("u", 401, "c", {}, None))
    assert not tb._is_permanent(OSError("network"))


def test_add_marks_source_telegram(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    tb.handle(f"/add {work} :: x", conn, tb.CHAT_ID)
    assert tq.list_tasks(conn)[0]["source"] == "telegram"


def test_confirm_with_no_children_mentions_waiting(tmp_path):
    conn = _conn(tmp_path)
    pid = tq.add_task(conn, "母", "x", kind="orchestrator")
    tq.set_status(conn, pid, "blocked")
    out = tb.handle(f"/confirm {pid}", conn, tb.CHAT_ID)
    assert "沒有" in out or "還在" in out


def test_drain_discards_backlog():
    """啟動時要丟棄積壓訊息，否則停機期間的指令會被重播（重複建任務／花錢）。"""
    seen = []

    def fake_fetch(token, offset, timeout=30):
        seen.append(offset)
        return [{"update_id": 5}, {"update_id": 6}] if offset == 0 else []

    assert tb._drain("tok", fake_fetch) == 7   # 下一個要用的 offset
