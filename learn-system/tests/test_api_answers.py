import app as app_module
import pytest

from learn_system import db, tutor
from app import create_app


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_spawn_map_generation",
                        lambda flask_app, did: app_module._generate_map(did))
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


def make_question(client, cid, monkeypatch, goal_type, payload, reference):
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": payload, "reference_answer": reference})
    return client.post(f"/api/concepts/{cid}/questions", json={"goal_type": goal_type}).get_json()["question"]["id"]


def test_write_answer_graded_by_real_execution(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "def add(a, b): return a + b"})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_write_answer_wrong_when_test_fails(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "def add(a, b): return a - b"})
    assert r.get_json()["attempt"]["verdict"] == "wrong"


def test_exam_answer_graded_by_claude(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "閉包會捕捉定義環境的變數")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "partial", "feedback": "少講了 late binding", "root_cause": "未掌握 late binding"})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "就是函式包變數"})
    body = r.get_json()
    assert body["attempt"]["verdict"] == "partial"
    assert body["attempt"]["root_cause"] == "未掌握 late binding"


def test_mastery_updates_after_answers(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "好", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_four_of_five_is_mastered(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    verdicts = iter(["correct", "correct", "correct", "correct", "wrong"])
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": next(verdicts), "feedback": "f", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_timeout_is_not_counted_as_wrong(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    # 逾時代表「沒跑完」而非「寫錯」。判成 wrong 會污染掌握度，故必須 408
    # 且不留 attempt。（真正的 5 秒逾時由 test_executor.py 覆蓋，這裡只驗端點。）
    monkeypatch.setattr(app_module, "run_python",
                        lambda *a, **k: {"ok": False, "stdout": None, "stderr": "", "timed_out": True})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "while True: pass"})
    assert r.status_code == 408
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["status"] == "untested"


def test_write_question_without_executable_falls_back_to_claude(ctx, monkeypatch):
    client, did, cid = ctx
    # 領域不可執行時，就算題型是 write 也沒有客觀判準，只能交給 Agent 批改
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "ok", "root_cause": None})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "純文字作答"})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_attempt_is_persisted(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "wrong", "feedback": "f", "root_cause": "rc"})
    client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert len(attempts) == 1
    assert attempts[0]["verdict"] == "wrong"
