import store
from jobs import daily
from plans import PLANS

def q(t, m): return 10.0
def fx(): return 32.0

def test_due_tranche_first_day_is_one():
    assert daily.due_tranche("2026-07-24", "2026-07-24", 6) == 1

def test_due_tranche_next_month_same_day_is_two():
    assert daily.due_tranche("2026-07-24", "2026-08-24", 6) == 2

def test_due_tranche_non_matching_day_none():
    assert daily.due_tranche("2026-07-24", "2026-08-10", 6) is None

def test_due_tranche_after_six_none():
    assert daily.due_tranche("2026-07-24", "2027-02-24", 6) is None

def test_run_day_first_day_builds_and_snapshots():
    conn = store.connect(":memory:")
    plan = PLANS["A"]
    store.create_account(conn, "A", plan.name, "A", "2026-07-24", plan.capital_twd)
    snap = daily.run_day(conn, "A", "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    # 建帳日：6 檔 lump + 7 檔 DCA 第1期 = 13 筆
    assert len(trades) == 13
    assert abs(snap["total_value_twd"] - 9_000_000) < 5.0
