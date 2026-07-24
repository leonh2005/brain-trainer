import math
from plans import PLANS, Plan, Target

def test_two_plans_exist():
    assert set(PLANS.keys()) == {"A", "B"}

def test_account_a_totals_and_methods():
    a = PLANS["A"]
    assert isinstance(a, Plan)
    assert a.capital_twd == 9_000_000
    assert a.dca_months == 6
    # 金額加總 = 本金（容忍四捨五入 ±1 元）
    assert math.isclose(sum(t.target_twd for t in a.targets), 9_000_000, abs_tol=1.0)
    # 13 檔標的
    assert len(a.targets) == 13
    # 一次建倉:00864B + 5 檔個股
    lump = {t.ticker for t in a.targets if t.build_method == "lump"}
    assert lump == {"00864B", "BE", "SNDK", "CORZ", "IREN", "CRWV"}
    # DCA:7 檔 ETF
    dca = {t.ticker for t in a.targets if t.build_method == "dca"}
    assert dca == {"XLP", "XLU", "GLD", "EFV", "EWJ", "VWO", "0050"}

def test_account_a_market_tagging():
    a = PLANS["A"]
    tw = {t.ticker for t in a.targets if t.market == "TW"}
    assert tw == {"00864B", "0050"}

def test_account_b_totals():
    b = PLANS["B"]
    assert b.capital_twd == 1_000_000
    assert math.isclose(sum(t.target_twd for t in b.targets), 1_000_000, abs_tol=1.0)
    assert {t.ticker for t in b.targets} == {"CRM", "MSFT", "NOW", "AAPL", "PLTR"}
    assert all(t.build_method == "lump" for t in b.targets)
    assert all(t.market == "US" for t in b.targets)
