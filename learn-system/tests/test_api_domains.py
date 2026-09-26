import app as app_module
import pytest

from learn_system import tutor


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


def test_override_executable(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(executable=False))
    did = client.post("/api/domains", json={"name": "econ", "goals": ["principle"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 0
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 1
