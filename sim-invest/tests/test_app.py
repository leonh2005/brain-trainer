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

def test_nav_unknown_account_404(tmp_path, monkeypatch):
    db = tmp_path / "t2.db"
    monkeypatch.setenv("SIM_DB", str(db))
    import store as _s
    _s.connect(str(db)).close()
    import importlib, app as appmod
    importlib.reload(appmod)
    c = appmod.app.test_client()
    assert c.get("/api/account/ZZZ/nav").status_code == 404


def _app_with_mock_quotes(tmp_path, monkeypatch, name="t3.db"):
    db = tmp_path / name
    monkeypatch.setenv("SIM_DB", str(db))
    import importlib, app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod.quotes, "get_quote", lambda ticker, market: 10.0)
    monkeypatch.setattr(appmod.quotes, "get_fx", lambda: 32.0)
    return appmod.app.test_client()


def test_buy_creates_position(tmp_path, monkeypatch):
    c = _app_with_mock_quotes(tmp_path, monkeypatch)
    res = c.post("/api/account/B/buy", json={"ticker": "ADBE", "market": "US", "amount_twd": 3200})
    assert res.status_code == 200
    holdings = {h["ticker"]: h for h in res.get_json()["holdings"]}
    assert abs(holdings["ADBE"]["shares"] - 10.0) < 1e-6  # 3200 / (10*32)

def test_buy_over_available_cash_400(tmp_path, monkeypatch):
    c = _app_with_mock_quotes(tmp_path, monkeypatch, "t4.db")
    res = c.post("/api/account/B/buy", json={"ticker": "ADBE", "market": "US", "amount_twd": 999_999_999})
    assert res.status_code == 400

def test_sell_without_holding_400(tmp_path, monkeypatch):
    c = _app_with_mock_quotes(tmp_path, monkeypatch, "t5.db")
    res = c.post("/api/account/B/sell", json={"ticker": "ADBE", "market": "US", "shares": 1})
    assert res.status_code == 400

def test_buy_then_sell_realizes_pnl(tmp_path, monkeypatch):
    c = _app_with_mock_quotes(tmp_path, monkeypatch, "t6.db")
    c.post("/api/account/B/buy", json={"ticker": "ADBE", "market": "US", "amount_twd": 3200})
    res = c.post("/api/account/B/sell", json={"ticker": "ADBE", "market": "US", "shares": 5})
    assert res.status_code == 200
    data = res.get_json()
    assert data["account"]["realized_pnl_twd"] == 0.0  # 賣價=買價，損益為0
    holdings = {h["ticker"]: h for h in data["holdings"]}
    assert abs(holdings["ADBE"]["shares"] - 5.0) < 1e-6
