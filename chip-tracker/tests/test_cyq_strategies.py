# -*- coding: utf-8 -*-
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cyq
import history_loader
import strategies


def _frame(days=220):
    """穩定上漲、每日換手 2% 的合成 K 線。"""
    rows = []
    price = 100.0
    for i in range(days):
        o = price
        c = price * 1.003
        rows.append({
            "date": f"2025-{1 + i // 28 % 12:02d}-{i % 28 + 1:02d}",
            "open": o, "high": c * 1.005, "low": o * 0.995, "close": c,
            "volume": 2_000_000, "amount": c * 2_000_000,
            "amplitude": 1.0, "turnover": 2.0,
        })
        price = c
    return pd.DataFrame(rows)


def test_build_rows_amplitude_and_turnover():
    prices = [
        {"date": "2026-10-19", "open": 10, "max": 11, "min": 9, "close": 10,
         "Trading_Volume": 1_000_000, "Trading_money": 10_000_000},
        {"date": "2026-10-20", "open": 10, "max": 12, "min": 10, "close": 11,
         "Trading_Volume": 500_000, "Trading_money": 5_500_000},
    ]
    rows = history_loader.build_rows(prices, {"2026-10-01": 10_000_000},
                                     {"2026-10-20": (5, -2, 1)}, prev_close=10.0)
    assert rows[0]["amplitude"] == pytest.approx(20.0)   # (11-9)/10
    assert rows[1]["amplitude"] == pytest.approx(20.0)   # (12-10)/前一日收盤10
    assert rows[1]["turnover"] == pytest.approx(5.0)     # 500k / 10M
    assert rows[1]["total_net"] == 4


def test_build_rows_drops_zero_price_rows():
    prices = [
        {"date": "2026-10-19", "open": 0, "max": 0, "min": 0, "close": 0,
         "Trading_Volume": 1000, "Trading_money": 0},
        {"date": "2026-10-20", "open": 10, "max": 11, "min": 9, "close": 10,
         "Trading_Volume": 1000, "Trading_money": 10000},
    ]
    rows = history_loader.build_rows(prices, {}, {})
    assert len(rows) == 1 and rows[0]["date"] == "2026-10-20"


def test_cyq_distribution_invariants():
    r = cyq.compute_from_rows(_frame().to_dict("records"))
    assert sum(r["x"]) == pytest.approx(100.0, abs=0.01)
    assert r["benefit_part"] + r["overhead_ratio"] == pytest.approx(1.0, abs=0.001)
    assert r["y"][0] <= r["avg_cost"] <= r["y"][-1]
    assert r["percent_chips"]["90"]["price_range"][0] <= r["percent_chips"]["70"]["price_range"][0]
    assert r["percent_chips"]["70"]["price_range"][1] <= r["percent_chips"]["90"]["price_range"][1]


def test_cyq_rejects_missing_turnover():
    df = _frame()
    df["turnover"] = None
    with pytest.raises(ValueError, match="換手率"):
        cyq.compute_from_rows(df.to_dict("records"))


def test_prepare_frame_drops_bad_rows_and_computes_p_change():
    rows = [
        {"date": "2026-10-17", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100,
         "foreign_net": 0, "trust_net": 0, "dealer_net": 0},
        {"date": "2026-10-18", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0,
         "foreign_net": 0, "trust_net": 0, "dealer_net": 0},
        {"date": "2026-10-20", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 100,
         "foreign_net": 0, "trust_net": 0, "dealer_net": 0},
    ]
    df = strategies.prepare_frame(rows)
    assert list(df["date"]) == ["2026-10-17", "2026-10-20"]
    assert df.iloc[-1]["p_change"] == pytest.approx(10.0)


def test_low_backtrace_increase_can_fire():
    """上游把 previous_open 初始化成 -1000000，會讓此策略永遠 False；修正後應有訊號。"""
    rows = []
    price = 100.0
    for i in range(65):
        o = price
        c = price * 1.02
        rows.append({"date": f"2026-{(i // 28) + 1:02d}-{i % 28 + 1:02d}", "open": o,
                     "high": c, "low": o, "close": c, "volume": 1000,
                     "foreign_net": 0, "trust_net": 0, "dealer_net": 0})
        price = c
    df = strategies.prepare_frame(rows)
    assert strategies.low_backtrace_increase(("TEST", "測試"), df, date=df.iloc[-1]["date"]) is True
