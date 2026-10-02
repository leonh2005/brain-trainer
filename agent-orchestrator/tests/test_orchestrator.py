import stat

import pytest

import orchestrator
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


GOOD_JSON = (
    '{"subtasks": ['
    '{"title": "查產品", "spec": "查市面上產品", "cwd": "/tmp", "depends_on": []},'
    '{"title": "彙整", "spec": "寫成報告", "cwd": "/tmp", "depends_on": [0]}'
    ']}'
)


def test_parse_subtasks_plain_json():
    subs = orchestrator.parse_subtasks(GOOD_JSON)
    assert len(subs) == 2
    assert subs[0]["title"] == "查產品"
    assert subs[1]["depends_on"] == [0]


def test_parse_subtasks_strips_code_fence():
    fenced = "```json\n" + GOOD_JSON + "\n```"
    assert len(orchestrator.parse_subtasks(fenced)) == 2


def test_parse_subtasks_rejects_garbage():
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks("我很樂意幫忙，但先讓我說明一下...")


def test_parse_subtasks_rejects_out_of_range_dep():
    bad = '{"subtasks": [{"title": "a", "spec": "s", "depends_on": [5]}]}'
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_decompose_creates_blocked_children_and_blocks_parent(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "研究並報告", cwd=str(tmp_path), kind="orchestrator")
    child_ids = orchestrator.decompose(conn, tq.get_task(conn, pid),
                                       claude_bin=fake, timeout=10)
    assert len(child_ids) == 2
    assert tq.get_task(conn, pid)["status"] == "blocked"
    c0 = tq.get_task(conn, child_ids[0])
    assert c0["status"] == "blocked"
    assert c0["parent_id"] == pid
    # 第二個子任務的 depends_on 已映射成真實 id，不是索引
    c1 = tq.get_task(conn, child_ids[1])
    assert c1["depends_on"] == [child_ids[0]]


def test_decompose_marks_parent_failed_on_bad_output(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo 'not json at all'\n")
    pid = tq.add_task(conn, "大任務", "研究並報告", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, pid)
    assert got["status"] == "failed"
    assert got["error"]


def test_confirm_releases_children(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    child_ids = orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    n = orchestrator.confirm(conn, pid)
    assert n == 2
    assert tq.get_task(conn, child_ids[0])["status"] == "pending"
    assert tq.get_task(conn, pid)["status"] == "blocked"  # 母任務仍在等子任務


def test_confirm_rejects_non_orchestrator(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "普通任務", "x")
    with pytest.raises(ValueError):
        orchestrator.confirm(conn, tid)


def test_confirm_rejects_missing(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        orchestrator.confirm(conn, "nope")


def test_summarize_writes_parent_result(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '總結：一切完成'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    tq.set_status(conn, pid, "blocked")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="子任務一做好了", parent_id=pid)

    ok = orchestrator.summarize(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert ok
    got = tq.get_task(conn, pid)
    assert got["status"] == "done"
    assert "總結" in got["result"]


def test_summarize_fails_parent_when_child_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '不該被呼叫'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "failed", error="壞了", parent_id=pid)
    orchestrator.summarize(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, pid)["status"] == "failed"
