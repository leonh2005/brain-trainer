import sqlite3

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


def test_source_urls_are_persisted_when_verifying(client, monkeypatch):
    """勾了查證時，_generate_map 要把模型給的出處寫進 concepts.source_urls。

    在此之前這個欄位只有一個測試在寫，生成路徑整條都沒有碰它——「需要引用
    出處」於是只等於提示詞多一句話，前端什麼都看不到。
    """
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "GIL", "section": "consensus", "description": "d",
                      "source_urls": ["https://docs.python.org/3/"]}]})
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": True}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["source_urls"] == [
        "https://docs.python.org/3/"]


def test_source_urls_stay_empty_without_verification(client, monkeypatch):
    """沒勾查證時行為不變：沒有出處、DB 欄位是 NULL。"""
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "GIL", "section": "consensus", "description": "d",
                      "source_urls": ["https://模型自己多給的"]}]})
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["source_urls"] is None


def test_regenerate_clears_partial_concepts_and_ends_ready(client, monkeypatch, tmp_path):
    """failed 不是終態：中斷後可以重試，而且不從頭疊上去。

    上一次跑到一半留下的概念必須先清掉，否則新地圖會疊在舊的上面。
    """
    def boom(*a, **k):
        raise tutor.TutorError("壞掉了")

    monkeypatch.setattr(tutor, "generate_map", boom)
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["status"] == "failed"

    conn = db.connect(tmp_path / "t.db")
    try:
        db.create_concept(conn, did, "半套的概念", "consensus")
    finally:
        conn.close()

    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=3))
    r = client.post(f"/api/domains/{did}/regenerate")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "ready"
    assert [c["name"] for c in body["concepts"]] == ["c0", "c1", "c2"]


def test_regenerate_is_rejected_while_generating(client, monkeypatch, tmp_path):
    """生成中再觸發一次會起第二條執行緒：兩邊同時寫入概念（疊成兩份），而且舊的
    那條最後把狀態寫回 ready，蓋掉新的 generating。前端按鈕雖然關著，但直呼
    API（或舊分頁）到得了，故在後端擋。
    """
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=2))
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": False}).get_json()["id"]
    conn = db.connect(tmp_path / "t.db")
    try:
        db.set_domain_status(conn, did, "generating")
    finally:
        conn.close()

    r = client.post(f"/api/domains/{did}/regenerate")
    assert r.status_code == 409
    body = client.get(f"/api/domains/{did}").get_json()
    assert len(body["concepts"]) == 2  # 原本那份沒被清掉


def test_regenerate_unknown_domain_404s(client):
    r = client.post("/api/domains/9999/regenerate")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_startup_reconciles_domains_stuck_in_generating(tmp_path, monkeypatch):
    """行程重啟後，卡在 generating 的領域要被標成 failed。

    生成執行緒隨行程死亡，資料列卻留在 generating；這個狀態在 regenerate 端點被擋
    （409），刪除成了唯一出路。開機時把這種殘骸標成 failed，使用者才能按「重新生成」
    復原。ready 的領域不受影響——它沒有卡住的執行緒，動它就是摧毀練習紀錄。
    """
    db_path = tmp_path / "t.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        stuck = db.create_domain(conn, "python", ["write"], False)  # 預設就是 generating
        ready = db.create_domain(conn, "econ", ["principle"], False)
        db.set_domain_status(conn, ready, "ready")
    finally:
        conn.close()

    # 生成改為同步執行，讓 regenerate 的後續斷言可預期
    monkeypatch.setattr(
        app_module,
        "_spawn_map_generation",
        lambda flask_app, domain_id: app_module._generate_map(domain_id),
    )
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=1))
    flask_app = app_module.create_app(db_path=db_path)
    flask_app.config["TESTING"] = True
    c = flask_app.test_client()

    assert c.get(f"/api/domains/{stuck}").get_json()["domain"]["status"] == "failed"
    assert c.get(f"/api/domains/{ready}").get_json()["domain"]["status"] == "ready"
    # 被救回來之後，復原路徑真的走得通
    assert c.post(f"/api/domains/{stuck}/regenerate").status_code == 200
    assert c.get(f"/api/domains/{stuck}").get_json()["domain"]["status"] == "ready"


def test_regenerate_is_rejected_on_a_ready_domain(client, monkeypatch, tmp_path):
    """ready 一律拒絕：這個動作會清掉概念、題目、作答，還會蓋掉 executable 覆寫。

    這些都是使用者的練習紀錄，不能靠「前端只畫給 failed 的按鈕」來保護——端點
    自己才是那個保證。原本這條測試斷言回 200，等於把破壞性行為寫成了規格。
    """
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=3))
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"],
                                            "verify_sources": False}).get_json()["id"]
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})

    conn = db.connect(tmp_path / "t.db")
    try:
        cid = db.list_concepts(conn, did)[0]["id"]
        qid = db.create_question(conn, cid, "exam", "問題", {}, "答案")
        db.create_attempt(conn, qid, "作答", "wrong", "回饋")
    finally:
        conn.close()

    r = client.post(f"/api/domains/{did}/regenerate")
    assert r.status_code == 409
    assert "error" in r.get_json()

    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "ready"
    assert [c["name"] for c in body["concepts"]] == ["c0", "c1", "c2"]
    assert body["domain"]["executable"] == 0
    conn = db.connect(tmp_path / "t.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
    finally:
        conn.close()


def test_override_executable(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(executable=False))
    did = client.post("/api/domains", json={"name": "econ", "goals": ["principle"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 0
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 1


def test_generation_marks_failed_when_connect_fails(tmp_path, monkeypatch):
    """連線建立失敗也必須收斂成 failed。

    原本 db.connect 在 try 之外，連線一失敗例外就逃出執行緒，資料列永遠停在
    schema 預設的 generating，前端會無限輪詢且沒有錯誤狀態可顯示。
    """
    import app as app_module

    db_file = tmp_path / "t.db"
    conn = db.connect(db_file)
    db.init_db(conn)
    did = db.create_domain(conn, "python", ["write"], False)
    conn.close()

    real_connect = db.connect
    calls = {"n": 0}

    def flaky_connect(db_path=None):
        calls["n"] += 1
        # 第一次（生成執行緒的那次）失敗，之後恢復，讓 _mark_failed 寫得進去
        if calls["n"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(db_path)

    monkeypatch.setattr(app_module, "_DB_PATH", db_file)
    monkeypatch.setattr(app_module.db, "connect", flaky_connect)

    app_module._generate_map(did)  # 不得拋出

    conn = real_connect(db_file)
    try:
        assert db.get_domain(conn, did)["status"] == "failed"
    finally:
        conn.close()
