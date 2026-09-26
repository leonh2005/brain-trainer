import app as app_module
import pytest

from learn_system import tutor

# test_code 依題目定義是 pytest 斷言（見 tutor.QUESTION_PROMPT），故寫成測試函式。
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
    app = app_module.create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def make_question(client, cid, monkeypatch, goal_type, payload, reference):
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": payload, "reference_answer": reference})
    return client.post(f"/api/concepts/{cid}/questions",
                       json={"goal_type": goal_type}).get_json()["question"]["id"]


def test_mistakes_empty_for_fresh_domain(ctx):
    client, did, cid = ctx
    assert client.get(f"/api/domains/{did}/mistakes").get_json() == {"mistakes": []}


def test_mistakes_lists_wrong_answers_newest_first(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    first = client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER}).get_json()["attempt"]["id"]
    second = client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER}).get_json()["attempt"]["id"]

    mistakes = client.get(f"/api/domains/{did}/mistakes").get_json()["mistakes"]
    assert [m["id"] for m in mistakes] == [second, first]
    assert mistakes[0]["concept_name"] == "閉包"
    assert mistakes[0]["verdict"] == "wrong"
    assert mistakes[0]["created_at"]


def test_correct_answers_are_not_mistakes(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    client.post(f"/api/questions/{qid}/answer", json={"answer": CORRECT_ANSWER})
    client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER})

    mistakes = client.get(f"/api/domains/{did}/mistakes").get_json()["mistakes"]
    assert [m["verdict"] for m in mistakes] == ["wrong"]


def test_partial_answers_are_mistakes(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "閉包會捕捉定義環境的變數")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "partial", "feedback": "少講了 late binding", "root_cause": "未掌握 late binding"})
    client.post(f"/api/questions/{qid}/answer", json={"answer": "就是函式包變數"})

    mistakes = client.get(f"/api/domains/{did}/mistakes").get_json()["mistakes"]
    assert [m["verdict"] for m in mistakes] == ["partial"]
    assert mistakes[0]["root_cause"] == "未掌握 late binding"


def test_mistakes_are_scoped_to_the_domain(ctx, monkeypatch):
    client, did, cid = ctx
    other = client.post("/api/domains", json={"name": "econ", "goals": ["exam"],
                                              "verify_sources": False}).get_json()["id"]
    other_cid = client.get(f"/api/domains/{other}").get_json()["concepts"][0]["id"]
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": {}, "reference_answer": "a"})
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "wrong", "feedback": "f", "root_cause": None})
    other_qid = client.post(f"/api/concepts/{other_cid}/questions",
                            json={"goal_type": "exam"}).get_json()["question"]["id"]
    client.post(f"/api/questions/{other_qid}/answer", json={"answer": "x"})

    assert client.get(f"/api/domains/{did}/mistakes").get_json()["mistakes"] == []
    assert len(client.get(f"/api/domains/{other}/mistakes").get_json()["mistakes"]) == 1


def test_mistakes_query_is_bounded(ctx, monkeypatch):
    """錯題會無限累積，查詢必須帶明確上限，不能整表撈回。"""
    client, did, cid = ctx
    real = app_module.db.list_attempts_for_domain
    seen = {}

    def spy(conn, domain_id, limit=None):
        seen["limit"] = limit
        return real(conn, domain_id, limit=limit) if limit is not None else real(conn, domain_id)

    monkeypatch.setattr(app_module.db, "list_attempts_for_domain", spy)
    client.get(f"/api/domains/{did}/mistakes")
    assert isinstance(seen["limit"], int) and seen["limit"] > 0


def test_mistakes_unknown_domain_404(ctx):
    client, did, cid = ctx
    r = client.get("/api/domains/9999/mistakes")
    assert r.status_code == 404
    assert r.get_json()["error"] == "找不到領域"


def test_mistakes_carry_the_submitted_answer(ctx, monkeypatch):
    """錯題本要顯示作答者寫了什麼，否則只看到「錯了」沒有回頭複習的價值。"""
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER})
    mistakes = client.get(f"/api/domains/{did}/mistakes").get_json()["mistakes"]
    assert mistakes[0]["answer"] == WRONG_ANSWER
