import app as app_module
import pytest

from learn_system import tutor


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_spawn_map_generation",
                        lambda flask_app, did: app_module._generate_map(did))
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": None}],
    })
    app = app_module.create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def test_explain_generates_and_caches(ctx, monkeypatch):
    client, did, cid = ctx
    calls = []
    monkeypatch.setattr(tutor, "explain_concept", lambda *a, **k: calls.append(1) or "閉包是…")
    assert client.post(f"/api/concepts/{cid}/explain").get_json()["description"] == "閉包是…"
    assert client.post(f"/api/concepts/{cid}/explain").get_json()["description"] == "閉包是…"
    assert len(calls) == 1  # 第二次走快取


def test_explain_failure_returns_502(ctx, monkeypatch):
    client, did, cid = ctx

    def boom(*a, **k):
        raise tutor.TutorError("壞了")

    monkeypatch.setattr(tutor, "explain_concept", boom)
    assert client.post(f"/api/concepts/{cid}/explain").status_code == 502


def test_chat_stores_session(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "chat", lambda *a, **k: ("嗨", "sess-1"))
    assert client.post(f"/api/domains/{did}/chat", json={"message": "hi", "concept_id": cid}).get_json()["reply"] == "嗨"
    seen = {}
    monkeypatch.setattr(tutor, "chat", lambda d, c, m, sid: seen.update(sid=sid) or ("again", "sess-1"))
    client.post(f"/api/domains/{did}/chat", json={"message": "again", "concept_id": cid})
    assert seen["sid"] == "sess-1"


def test_explain_unknown_concept_404(ctx):
    client, did, cid = ctx
    assert client.post("/api/concepts/9999/explain").status_code == 404


def test_explain_empty_response_not_cached(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: ("   \n", None))
    assert client.post(f"/api/concepts/{cid}/explain").status_code == 502
    # 空回應不可寫進 description，否則之後永遠走快取吐出空字串
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["description"] is None


def test_chat_rejects_empty_message(ctx):
    client, did, cid = ctx
    assert client.post(f"/api/domains/{did}/chat", json={"message": "   "}).status_code == 400


def test_chat_unknown_domain_404(ctx):
    client, did, cid = ctx
    assert client.post("/api/domains/9999/chat", json={"message": "hi"}).status_code == 404


def test_chat_writes_session_only_when_changed(ctx, monkeypatch):
    client, did, cid = ctx
    real = app_module.db.set_domain_chat_session
    writes = []

    def spy(conn, domain_id, session_id):
        writes.append(session_id)
        real(conn, domain_id, session_id)

    monkeypatch.setattr(app_module.db, "set_domain_chat_session", spy)
    monkeypatch.setattr(tutor, "chat", lambda *a, **k: ("嗨", "sess-1"))
    client.post(f"/api/domains/{did}/chat", json={"message": "hi"})
    client.post(f"/api/domains/{did}/chat", json={"message": "hi"})
    assert writes == ["sess-1"]
