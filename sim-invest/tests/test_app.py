import os, store
from plans import PLANS

def test_health_and_accounts(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("SIM_DB", str(db))
    # 先建好帳戶與一筆快照
    conn = store.connect(str(db))
    store.create_account(conn, "A", PLANS["A"].name, "A", "2026-07-24", 9_000_000)
    store.add_snapshot(conn, "A", "2026-07-24", 9_000_000, 5_000_000, 0.0, "{}")
    conn.close()
    import importlib, app as appmod
    importlib.reload(appmod)
    c = appmod.app.test_client()
    assert c.get("/api/health").get_json()["status"] == "ok"
    ids = [a["id"] for a in c.get("/api/accounts").get_json()]
    assert ids == ["A", "B"]
    nav = c.get("/api/account/A/nav").get_json()
    assert nav[0]["total_value_twd"] == 9_000_000
