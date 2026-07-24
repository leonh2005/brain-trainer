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
