import json

import app as app_module
import pytest

from learn_system import db, tutor
from app import create_app


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_spawn_map_generation",
        lambda flask_app, domain_id: app_module._generate_map(domain_id),
    )
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": "d"}],
    })
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def test_create_exam_question(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "什麼是閉包", "payload": {}, "reference_answer": "函式記住其定義環境"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "exam"})
    assert r.status_code == 200
    assert r.get_json()["question"]["prompt"] == "什麼是閉包"


def test_write_question_validates_by_running_tests(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "寫 counter",
        "payload": {"starter_code": "", "test_code": "assert 1 == 2, 'fail'"},
        "reference_answer": "x"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "write"})
    assert r.status_code == 422
    assert "題目無效" in r.get_json()["error"]


def test_read_question_rejects_fake_bug(ctx, monkeypatch):
    client, did, cid = ctx
    # 原版與修正版行為一致 → 假的 bug
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "找 bug",
        "payload": {"code_snippet": "print('x')", "fixed_code": "print('x')", "bug_description": "假的"},
        "reference_answer": "假的"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "read"})
    assert r.status_code == 422


def test_read_question_accepts_real_bug(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "找 bug",
        "payload": {"code_snippet": "raise ValueError('bug')", "fixed_code": "print('ok')",
                    "bug_description": "不該拋錯"},
        "reference_answer": "不該拋錯"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "read"})
    assert r.status_code == 200


def test_read_question_accepts_wrong_value_bug(ctx, monkeypatch):
    client, did, cid = ctx
    # 原版不拋錯但「輸出錯誤值」——這仍是真 bug，不可誤判為無效題
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "找 bug",
        "payload": {"code_snippet": "print(1)", "fixed_code": "print(2)", "bug_description": "值錯了"},
        "reference_answer": "值錯了"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "read"})
    assert r.status_code == 200


def test_principle_question_accepts_a_runnable_snippet(ctx, monkeypatch):
    """可執行的程式碼才有真實輸出可當批改錨點（見 answer 端點）。"""
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "預測輸出", "payload": {"code_snippet": "print(1 + 1)"}, "reference_answer": "2"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "principle"})
    assert r.status_code == 200


def test_principle_question_with_unrunnable_snippet_is_rejected(ctx, monkeypatch):
    """跑不動的程式碼沒有「真實輸出」可對照，學習者的預測無從批改。

    write／read 題在建立時都會真的跑一次，principle 題同樣必須。
    """
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "預測輸出", "payload": {"code_snippet": "print(1 +"}, "reference_answer": "3"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "principle"})
    assert r.status_code == 422
    assert "題目無效" in r.get_json()["error"]


def test_principle_question_accepts_a_raising_snippet(ctx, monkeypatch):
    """「這段程式會拋出什麼例外」是有效的 principle 題。

    拋例外是「執行了然後失敗」，不是「跑不動」——stderr 的 traceback 就是可
    對照的真實輸出（見 answer 端點的 stdout or stderr）。若建立時要求結束碼
    為 0，這一類題目會永遠出不出來，與批改端互相矛盾。
    """
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "預測結果", "payload": {"code_snippet": "print(1 / 0)"}, "reference_answer": "會拋錯"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "principle"})
    assert r.status_code == 200


def test_principle_question_without_snippet_is_rejected(ctx, monkeypatch):
    """走正式路徑（真 generate_question）：缺 code_snippet 的 principle 題在
    出題階段就會被 tutor 擋下，端點回 502，不會入庫。
    """
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (
        json.dumps({"prompt": "預測輸出", "payload": {}, "reference_answer": "3"}), None))
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "principle"})
    assert r.status_code == 502
    assert "code_snippet" in r.get_json()["error"]


def test_unknown_goal_type_is_rejected(ctx, monkeypatch):
    client, did, cid = ctx
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "亂寫"})
    assert r.status_code == 400


def test_non_object_json_returns_502(ctx, monkeypatch):
    client, did, cid = ctx
    # 合法 JSON 但不是物件：上游壞掉 → 502，絕不能讓 AttributeError 變成 500
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: ("[1, 2, 3]", None))
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "exam"})
    assert r.status_code == 502


def test_write_question_accepts_valid_reference_answer(ctx, monkeypatch):
    client, did, cid = ctx
    payload = {
        "prompt": "寫一個回傳 1 的函式",
        "payload": {"starter_code": "",
                    "test_code": "from solution import counter\ndef test_counter():\n    assert counter() == 1"},
        "reference_answer": "def counter():\n    return 1",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "write"})
    assert r.status_code == 200
    assert r.get_json()["question"]["prompt"] == "寫一個回傳 1 的函式"


def test_fenced_reference_answer_is_accepted_and_stored_clean(ctx, monkeypatch):
    client, did, cid = ctx
    payload = {
        "prompt": "寫一個回傳 1 的函式",
        "payload": {"starter_code": "",
                    "test_code": "```python\nfrom solution import counter\ndef test_counter():\n    assert counter() == 1\n```"},
        "reference_answer": "```python\ndef counter():\n    return 1\n```",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "write"})
    assert r.status_code == 200
    # Task 7 拿 payload["test_code"] 批改學習者答案，圍欄必須在入庫前清掉
    saved = r.get_json()["question"]
    assert "```" not in saved["payload"]["test_code"]
    assert saved["payload"]["test_code"].startswith("from solution")


def test_question_is_persisted(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": {}, "reference_answer": "a"})
    qid = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "exam"}).get_json()["question"]["id"]
    conn = db.connect(client.application.config["DB_PATH"])
    assert db.get_question(conn, qid)["prompt"] == "q"
    conn.close()
