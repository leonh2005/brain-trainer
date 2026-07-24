import store

def test_create_and_get_account():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "測試A", "A", "2026-07-24", 9_000_000)
    row = store.get_account(conn, "A")
    assert row["name"] == "測試A"
    assert row["capital_twd"] == 9_000_000

def test_add_and_get_trades():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "A", "A", "2026-07-24", 9_000_000)
    store.add_trade(conn, "A", "2026-07-24", "BE", "US", 10.5, 40.0, 32.35, 13_587.0, 0)
    trades = store.get_trades(conn, "A")
    assert len(trades) == 1
    assert trades[0]["ticker"] == "BE"
    assert trades[0]["shares"] == 10.5

def test_snapshot_upsert_overwrites_same_date():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "A", "A", "2026-07-24", 9_000_000)
    store.add_snapshot(conn, "A", "2026-07-24", 100.0, 50.0, 0.0, "{}")
    store.add_snapshot(conn, "A", "2026-07-24", 200.0, 40.0, 5.0, "{}")
    snaps = store.get_snapshots(conn, "A")
    assert len(snaps) == 1
    assert snaps[0]["total_value_twd"] == 200.0
    assert store.latest_snapshot(conn, "A")["total_value_twd"] == 200.0
