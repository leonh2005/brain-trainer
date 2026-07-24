import store
from jobs import daily
from plans import PLANS

def q(t, m): return 10.0
def fx(): return 32.0

def test_tranches_due_first_day_is_one():
    assert daily.tranches_due("2026-07-24", "2026-07-24", 6) == 1

def test_tranches_due_next_month_same_day_is_two():
    assert daily.tranches_due("2026-07-24", "2026-08-24", 6) == 2

def test_tranches_due_before_anniversary_still_one():
    assert daily.tranches_due("2026-07-24", "2026-08-10", 6) == 1

def test_tranches_due_missed_day_catches_up_next_day():
    assert daily.tranches_due("2026-07-24", "2026-08-25", 6) == 2

def test_tranches_due_month_end_clamp():
    assert daily.tranches_due("2026-01-31", "2026-02-28", 6) == 2

def test_tranches_due_caps_at_dca_months():
    assert daily.tranches_due("2026-07-24", "2027-02-24", 6) == 6

def test_tranches_due_today_before_start_is_one():
    assert daily.tranches_due("2026-07-24", "2026-07-23", 6) == 1

def test_run_day_first_day_builds_and_snapshots():
    conn = store.connect(":memory:")
    plan = PLANS["A"]
    store.create_account(conn, "A", plan.name, "A", "2026-07-24", plan.capital_twd)
    snap = daily.run_day(conn, "A", "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    # 建帳日：6 檔 lump + 7 檔 DCA 第1期 = 13 筆
    assert len(trades) == 13
    assert abs(snap["total_value_twd"] - 9_000_000) < 5.0

def test_run_day_catches_up_missed_tranches():
    conn = store.connect(":memory:")
    plan = PLANS["A"]
    store.create_account(conn, "A", plan.name, "A", "2026-07-24", plan.capital_twd)
    daily.run_day(conn, "A", "2026-07-24", q, fx)          # 第1期
    # 第2期(2026-08-24)排程漏跑，直到第3期當天(2026-09-24)才執行
    daily.run_day(conn, "A", "2026-09-24", q, fx)
    trades = store.get_trades(conn, "A")
    dca_trades = [t for t in trades if t["tranche_no"] >= 1]
    # 7 檔 dca 標的 * 3 期 = 21 筆
    assert len(dca_trades) == 21
    by_tranche = {}
    for t in dca_trades:
        by_tranche.setdefault(t["tranche_no"], 0)
        by_tranche[t["tranche_no"]] += 1
    assert by_tranche == {1: 7, 2: 7, 3: 7}

def test_run_day_is_idempotent_same_day():
    conn = store.connect(":memory:")
    plan = PLANS["A"]
    store.create_account(conn, "A", plan.name, "A", "2026-07-24", plan.capital_twd)
    daily.run_day(conn, "A", "2026-07-24", q, fx)
    daily.run_day(conn, "A", "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    assert len(trades) == 13
