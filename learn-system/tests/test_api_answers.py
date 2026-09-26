import app as app_module
import pytest

from learn_system import db, tutor
from app import create_app

# test_code 依題目定義是 pytest 斷言（見 tutor.QUESTION_PROMPT），故一律寫成
# 測試函式：模組層級的裸 assert pytest 不會收集，等於沒有測試可跑。
TEST_CODE = "from solution import add\ndef test_add():\n    assert add(1, 2) == 3"
CORRECT_ANSWER = "def add(a, b):\n    return a + b"
WRONG_ANSWER = "def add(a, b):\n    return a - b"


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
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": CORRECT_ANSWER})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_write_answer_wrong_when_test_fails(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER})
    assert r.get_json()["attempt"]["verdict"] == "wrong"


def test_write_answer_that_exits_the_process_is_not_correct(ctx, monkeypatch):
    """結束碼不可信：這些答案的行程結束碼都是 0，斷言一次都沒跑。

    `sys.exit(0)`／`raise SystemExit(0)` 會被 pytest 判成 INTERNALERROR，
    但 `os._exit(0)` 直接結束行程、pytest 來不及回報，結束碼就是 0。
    """
    client, did, cid = ctx
    for answer in ("import sys\nsys.exit(0)", "import os\nos._exit(0)", "raise SystemExit(0)"):
        qid = make_question(client, cid, monkeypatch, "write",
                            {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": answer})
        assert r.get_json()["attempt"]["verdict"] == "wrong", answer


def test_write_question_without_test_code_is_rejected(ctx, monkeypatch):
    """題目可能在不可執行時生成（驗證被跳過），之後才被翻成可執行。

    這種題目沒有任何判準，不能因為答案能執行完就判 correct。
    """
    client, did, cid = ctx
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": ""}, CORRECT_ANSWER)
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "print('anything')"})
    assert r.status_code == 422
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []


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
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    # 逾時代表「沒跑完」而非「寫錯」。判成 wrong 會污染掌握度，故必須 408
    # 且不留 attempt。（真正的 5 秒逾時由 test_executor.py 覆蓋，這裡只驗端點。）
    monkeypatch.setattr(app_module, "run_pytest",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "",
                                         "timed_out": True, "env_error": False})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "while True: pass"})
    assert r.status_code == 408
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["status"] == "untested"


def test_env_failure_is_not_counted_as_wrong(ctx, monkeypatch):
    # 執行環境失敗同樣不是「寫錯」（executor 以 env_error 區分），不得落 attempt
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    monkeypatch.setattr(app_module, "run_pytest",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "執行環境錯誤：x",
                                         "timed_out": False, "env_error": True})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.status_code == 503
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
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
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
