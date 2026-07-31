import json
import store, engine
from plans import PLANS

# 固定假報價：美股一律 $10、匯率 32、台股一律 50 元
def q(ticker, market): return 10.0
def fx(): return 32.0

def _fresh(account_id="A"):
    conn = store.connect(":memory:")
    plan = PLANS[account_id]
    store.create_account(conn, account_id, plan.name, account_id, "2026-07-24", plan.capital_twd)
    return conn, plan

def test_price_twd_us_applies_fx():
    from plans import Target
    t = Target("XLP", "US", "x", 900_000, "dca")
    assert engine.price_twd(t, q, fx) == 320.0   # 10 * 32

def test_price_twd_tw_no_fx():
    from plans import Target
    t = Target("0050", "TW", "x", 100, "dca")
    assert engine.price_twd(t, lambda tk, m: 50.0, fx) == 50.0

def test_target_shares():
    assert engine.target_shares(320.0, 32.0) == 10.0

def test_build_lump_only_lump_targets():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    # A 帳戶 lump 標的共 6 檔（00864B + 5 個股）
    assert len(trades) == 6
    assert {t["ticker"] for t in trades} == {"00864B","BE","SNDK","CORZ","IREN","CRWV"}
    # 每筆 tranche_no=0、cost_twd 約等於目標金額
    be = next(t for t in trades if t["ticker"] == "BE")
    assert be["tranche_no"] == 0
    assert abs(be["cost_twd"] - 412_222.22) < 1.0

def test_dca_tranche_invests_one_sixth():
    conn, plan = _fresh("A")
    engine.run_dca_tranche(conn, "A", plan, "2026-08-01", 1, q, fx)
    trades = store.get_trades(conn, "A")
    # A 帳戶 dca 標的 7 檔
    assert len(trades) == 7
    assert {t["ticker"] for t in trades} == {"XLP","XLU","GLD","EFV","EWJ","VWO","0050"}
    xlp = next(t for t in trades if t["ticker"] == "XLP")
    assert xlp["tranche_no"] == 1
    assert abs(xlp["cost_twd"] - 900_000/6) < 1.0   # 150,000

def test_six_tranches_fully_invest_dca_targets():
    conn, plan = _fresh("A")
    for n in range(1, 7):
        engine.run_dca_tranche(conn, "A", plan, f"2026-0{n}-01", n, q, fx)
    trades = [t for t in store.get_trades(conn, "A") if t["ticker"] == "EFV"]
    assert abs(sum(t["cost_twd"] for t in trades) - 1_413_333.33) < 1.0

def test_snapshot_all_lump_at_flat_price():
    # 只建 lump 部位，報價與建倉同價 → 未實現損益=0、現金=本金−已投入
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-24", q, fx)
    invested = sum(t.target_twd for t in plan.targets if t.build_method == "lump")
    assert abs(snap["cash_twd"] - (9_000_000 - invested)) < 1.0
    assert abs(snap["unrealized_pnl_twd"]) < 1.0
    assert abs(snap["total_value_twd"] - 9_000_000) < 1.0
    # 寫入 DB
    assert store.latest_snapshot(conn, "A")["date"] == "2026-07-24"

def test_snapshot_reflects_price_gain():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)      # 建倉價 $10
    # 之後美股漲一倍（$20），台股不變
    up = lambda tk, m: 20.0 if m == "US" else 10.0
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-25", up, fx)
    # lump 美股部位（BE/SNDK/CORZ/IREN/CRWV）市值翻倍，00864B（TW）不變
    us_cost = sum(t.target_twd for t in plan.targets
                  if t.build_method == "lump" and t.market == "US")
    assert abs(snap["unrealized_pnl_twd"] - us_cost) < 1.0        # 美股獲利 = 成本×100%

def test_snapshot_by_category_keys():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-24", q, fx)
    assert "AI基建衛星" in snap["by_category"]
    assert "短債防禦" in snap["by_category"]

def test_rebalance_flat_no_signal():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    for n in range(1, 7):
        engine.run_dca_tranche(conn, "A", plan, f"2026-0{n}-01", n, q, fx)
    # 全部建完、價格不變 → 各類比例=目標，無訊號
    engine.daily_snapshot(conn, "A", plan, "2026-12-01", q, fx)
    signals = engine.check_rebalance(conn, "A", plan)
    assert signals == []

def test_rebalance_satellite_take_profit():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)   # 衛星個股 $10 建倉
    # 衛星（美股個股）漲 2 倍 → 市值 2x 目標 > 1.5x → 觸發
    boom = lambda tk, m: 20.0 if m == "US" else 10.0
    engine.daily_snapshot(conn, "A", plan, "2026-08-01", boom, fx)
    signals = engine.check_rebalance(conn, "A", plan)
    assert any(s["type"] == "satellite_take_profit" for s in signals)

def test_rebalance_no_snapshot_returns_empty():
    conn, plan = _fresh("A")
    assert engine.check_rebalance(conn, "A", plan) == []

def test_valuate_returns_by_ticker_without_writing():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    snap = engine.valuate(conn, "A", plan, q, fx)
    assert "BE" in snap["by_ticker"]
    be = snap["by_ticker"]["BE"]
    assert abs(be["market_value"] - be["cost_twd"]) < 1.0   # 建倉價=報價，未實現損益=0
    assert be["shares"] > 0
    assert store.latest_snapshot(conn, "A") is None   # 唯讀，未寫入

def test_daily_snapshot_persists_by_ticker_json():
    import json
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    engine.daily_snapshot(conn, "A", plan, "2026-07-24", q, fx)
    row = store.latest_snapshot(conn, "A")
    by_ticker = json.loads(row["by_ticker_json"])
    assert "BE" in by_ticker
    assert "market_value" in by_ticker["BE"]

def test_add_position_converts_amount_to_shares():
    conn, plan = _fresh("A")
    result = engine.add_position(conn, "A", "2026-07-24", "AAPL", "US", 1000.0, q, fx)
    # 現價 $10 * fx 32 = 320/股 → 1000/320 = 3.125 股
    assert abs(result["shares"] - 3.125) < 1e-6
    hs = engine.holdings(conn, "A")
    assert abs(hs["AAPL"]["shares"] - 3.125) < 1e-6
    assert abs(hs["AAPL"]["cost_twd"] - 1000.0) < 1e-6

def test_close_position_by_shares_realizes_pnl():
    conn, plan = _fresh("A")
    engine.add_position(conn, "A", "2026-07-24", "AAPL", "US", 3200.0, q, fx)  # 10股 @ $10*32=320
    # 漲一倍賣出 5 股
    up = lambda tk, m: 20.0
    result = engine.close_position(conn, "A", "2026-07-25", "AAPL", "US",
                                   shares=5.0, quote_fn=up, fx_fn=fx)
    assert abs(result["shares_sold"] - 5.0) < 1e-6
    assert abs(result["proceeds_twd"] - 5.0 * 20.0 * 32.0) < 1e-6
    assert abs(result["realized_pnl_twd"] - 5.0 * 320.0) < 1e-6  # 均價320漲到640，賺320/股*5
    hs = engine.holdings(conn, "A")
    assert abs(hs["AAPL"]["shares"] - 5.0) < 1e-6
    assert abs(hs["AAPL"]["realized_pnl_twd"] - 5.0 * 320.0) < 1e-6

def test_close_position_caps_at_held_shares():
    conn, plan = _fresh("A")
    engine.add_position(conn, "A", "2026-07-24", "AAPL", "US", 3200.0, q, fx)  # 10股
    result = engine.close_position(conn, "A", "2026-07-25", "AAPL", "US",
                                   shares=999.0, quote_fn=q, fx_fn=fx)
    assert abs(result["shares_sold"] - 10.0) < 1e-6
    hs = engine.holdings(conn, "A")
    assert abs(hs["AAPL"]["shares"]) < 1e-6

def test_close_position_by_amount():
    conn, plan = _fresh("A")
    engine.add_position(conn, "A", "2026-07-24", "AAPL", "US", 3200.0, q, fx)  # 10股 @320
    result = engine.close_position(conn, "A", "2026-07-25", "AAPL", "US",
                                   amount_twd=1600.0, quote_fn=q, fx_fn=fx)  # 1600/320=5股
    assert abs(result["shares_sold"] - 5.0) < 1e-6

def test_close_position_no_holding_raises():
    conn, plan = _fresh("A")
    import pytest
    with pytest.raises(ValueError):
        engine.close_position(conn, "A", "2026-07-25", "AAPL", "US",
                              shares=1.0, quote_fn=q, fx_fn=fx)
