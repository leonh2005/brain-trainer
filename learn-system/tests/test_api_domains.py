import app as app_module
import pytest

from learn_system import db, tutor


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 背景生成改為同步執行，讓測試可預期
    monkeypatch.setattr(
        app_module,
        "_spawn_map_generation",
        lambda flask_app, domain_id: app_module._generate_map(domain_id),
    )
    flask_app = app_module.create_app(db_path=tmp_path / "t.db")
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def fake_map(executable=True, n=3):
    concepts = []
    for i in range(n):
        concepts.append({"name": f"c{i}", "section": "consensus", "description": "d"})
    return {"executable": executable, "concepts": concepts}


def test_health(client):
    assert client.get("/api/health").get_json() == {"ok": True}


def test_create_domain_returns_id(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map())
    r = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False})
    assert r.status_code == 200
    assert isinstance(r.get_json()["id"], int)


def test_created_domain_is_ready_with_concepts(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=3))
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "ready"
    assert body["domain"]["executable"] == 1
    assert len(body["concepts"]) == 3
    assert body["progress"]["mastery_pct"] == 0


def test_generation_failure_marks_failed(client, monkeypatch):
    def boom(*a, **k):
        raise tutor.TutorError("壞掉了")

    monkeypatch.setattr(tutor, "generate_map", boom)
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["status"] == "failed"


def test_unexpected_generation_error_marks_failed(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("非 TutorError 的意外錯誤")

    monkeypatch.setattr(tutor, "generate_map", boom)
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["status"] == "failed"


def test_empty_concepts_marks_failed(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {"executable": True, "concepts": []})
    did = client.post("/api/domains", json={"name": "x", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "failed"
    assert body["concepts"] == []


def test_list_domains_reports_mastery(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map())
    client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False})
    domains = client.get("/api/domains").get_json()["domains"]
    assert len(domains) == 1
    assert domains[0]["mastery_pct"] == 0
    assert domains[0]["concept_count"] == 3


def test_delete_domain_removes_it_and_its_children(client, monkeypatch, tmp_path):
    """刪除領域後，它的概念／題目／作答都要一起消失（schema 的 cascade）。

    直接查資料庫，因為端點只回 404——光是 404 分不出「子資料真的刪了」與
    「母列刪了、子列變成孤兒」。
    """
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map())
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]

    conn = db.connect(tmp_path / "t.db")
    try:
        concept_id = db.list_concepts(conn, did)[0]["id"]
        question_id = db.create_question(conn, concept_id, "exam", "問題", {}, "答案")
        db.create_attempt(conn, question_id, "作答", "wrong", "回饋")
    finally:
        conn.close()

    r = client.delete(f"/api/domains/{did}")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert client.get(f"/api/domains/{did}").status_code == 404
    assert client.get("/api/domains").get_json()["domains"] == []

    conn = db.connect(tmp_path / "t.db")
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("concepts", "questions", "attempts")}
    finally:
        conn.close()
    assert counts == {"concepts": 0, "questions": 0, "attempts": 0}


def test_delete_unknown_domain_404s(client):
    r = client.delete("/api/domains/9999")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_override_executable(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(executable=False))
    did = client.post("/api/domains", json={"name": "econ", "goals": ["principle"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 0
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 1
