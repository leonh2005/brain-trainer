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


def test_parse_subtasks_rejects_self_dependency():
    bad = '{"subtasks": [{"title": "a", "spec": "s", "depends_on": [0]}]}'
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_parse_subtasks_rejects_cycle():
    bad = ('{"subtasks": ['
           '{"title": "a", "spec": "s", "depends_on": [1]},'
           '{"title": "b", "spec": "s", "depends_on": [0]}]}')
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_parse_subtasks_rejects_non_dict_entries():
    bad = '{"subtasks": ["do a", "do b"]}'
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_parse_subtasks_rejects_missing_spec_and_title():
    bad = '{"subtasks": [{"depends_on": []}]}'
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_decompose_rejects_missing_cwd(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", kind="orchestrator")  # 沒給 cwd
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, pid)
    assert got["status"] == "failed"
    assert "工作目錄" in got["error"]


def test_decompose_retries_then_succeeds(tmp_path):
    """第一次呼叫失敗、第二次成功 → 母任務正常拆解。"""
    conn = _conn(tmp_path)
    counter = tmp_path / "n"
    fake = _fake_claude(
        tmp_path,
        f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n'
        f'if [ "$n" -lt 2 ]; then exit 1; fi\n'
        f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, pid)["status"] == "blocked"


def test_decompose_gives_up_after_retries(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "exit 1\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, pid)
    assert got["status"] == "failed"
    assert "拆解失敗" in got["error"]


def test_decompose_retry_does_not_duplicate_children(tmp_path):
    """重試成功後只會有一批子任務，不會因為重試而累積。"""
    conn = _conn(tmp_path)
    counter = tmp_path / "n"
    fake = _fake_claude(
        tmp_path,
        f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n'
        f'if [ "$n" -lt 2 ]; then exit 1; fi\n'
        f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert len(orchestrator.children_of(conn, pid)) == 2  # GOOD_JSON 有 2 個


def test_decompose_children_not_claimable_before_confirm(tmp_path):
    """拆解完的子任務在使用者 confirm 前，一個都不能被 claim_next 搶走。"""
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    children = orchestrator.decompose(conn, tq.get_task(conn, pid),
                                      claude_bin=fake, timeout=10)
    assert tq.claim_next(conn) is None
    for cid in children:
        assert tq.get_task(conn, cid)["status"] == "blocked"


def test_decompose_no_orphan_when_child_creation_fails(tmp_path, monkeypatch):
    """建立子任務中途失敗 → 不留可被認領的孤兒，母任務標 failed 不卡 running。"""
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")

    real_add = tq.add_task
    calls = {"n": 0}

    def flaky_add(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模擬建立子任務失敗")
        return real_add(*args, **kwargs)

    monkeypatch.setattr(tq, "add_task", flaky_add)
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    monkeypatch.undo()

    assert tq.claim_next(conn) is None                    # 沒有孤兒可被認領
    assert tq.get_task(conn, pid)["status"] == "failed"   # 母任務沒卡在 running
