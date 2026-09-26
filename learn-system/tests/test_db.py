import pytest

from learn_system import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    yield c
    c.close()


def test_create_and_get_domain(conn):
    did = db.create_domain(conn, "python", ["write", "exam"], verify_sources=False)
    d = db.get_domain(conn, did)
    assert d["name"] == "python"
    assert d["goals"] == ["write", "exam"]
    assert d["verify_sources"] == 0
    assert d["executable"] == 1
    assert d["status"] == "generating"


def test_list_domains(conn):
    db.create_domain(conn, "python", ["write"], False)
    db.create_domain(conn, "economics", ["principle"], False)
    names = [d["name"] for d in db.list_domains(conn)]
    assert names == ["python", "economics"]


def test_concepts_roundtrip(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    c = db.get_concept(conn, cid)
    assert c["name"] == "閉包"
    assert c["section"] == "consensus"
    assert c["status"] == "untested"
    assert c["description"] is None
    db.set_concept_description(conn, cid, "說明文字")
    assert db.get_concept(conn, cid)["description"] == "說明文字"


def test_concept_source_urls_json(conn):
    did = db.create_domain(conn, "python", ["write"], True)
    cid = db.create_concept(conn, did, "GIL", "consensus", source_urls=["https://x"])
    assert db.get_concept(conn, cid)["source_urls"] == ["https://x"]


def test_question_and_attempt(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "write", "寫一個 counter", {"test_code": "pass"}, "參考解")
    q = db.get_question(conn, qid)
    assert q["payload"] == {"test_code": "pass"}
    assert q["goal_type"] == "write"
    db.create_attempt(conn, qid, "我的答案", "wrong", "錯在 late binding", "混淆變數作用域")
    attempts = db.list_attempts_for_concept(conn, cid)
    assert len(attempts) == 1
    assert attempts[0]["root_cause"] == "混淆變數作用域"


def test_list_attempts_newest_first_and_limit(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "exam", "q", {}, "a")
    for i in range(7):
        db.create_attempt(conn, qid, f"ans{i}", "correct", "ok")
    got = db.list_attempts_for_concept(conn, cid, limit=5)
    assert len(got) == 5
    assert got[0]["answer"] == "ans6"


def test_delete_domain_cascades(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "exam", "q", {}, "a")
    db.create_attempt(conn, qid, "x", "correct", "ok")
    conn.execute("DELETE FROM domains WHERE id = ?", (did,))
    conn.commit()
    assert db.list_concepts(conn, did) == []
    assert db.get_question(conn, qid) is None
