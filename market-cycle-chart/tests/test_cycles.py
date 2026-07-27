# -*- coding: utf-8 -*-
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cycles as c


def test_months_between():
    assert c.months_between("2022-10", "2023-10") == 12
    assert c.months_between("2023-01", "2022-10") == -3
    assert c.months_between("2020-01", "2020-01") == 0


def test_sine_value_at_trough_is_minus_one():
    # 谷底錨點當月 = -1
    assert c.sine_value("2022-10", 48, "2022-10") == -1.0


def test_sine_value_at_half_period_is_peak():
    # 半週期(24個月)後 = 頂部 +1
    assert math.isclose(c.sine_value("2024-10", 48, "2022-10"), 1.0, abs_tol=1e-9)


def test_phase_progression():
    # 谷底 phase=0
    assert math.isclose(c.phase_at("2022-10", 48, "2022-10"), 0.0, abs_tol=1e-9)
    # 1/4 週期(12月)後 phase=0.25 上升段
    assert math.isclose(c.phase_at("2023-10", 48, "2022-10"), 0.25, abs_tol=1e-9)
    # 半週期 phase=0.5 頂部
    assert math.isclose(c.phase_at("2024-10", 48, "2022-10"), 0.5, abs_tol=1e-9)


def test_classify_phase():
    assert c.classify_phase(0.0) == "底部"
    assert c.classify_phase(0.25) == "上升段"
    assert c.classify_phase(0.5) == "頂部"
    assert c.classify_phase(0.75) == "下降段"
    assert c.classify_phase(0.95) == "底部"  # 接近回到谷底


def test_sine_series_length():
    months = ["2022-10", "2022-11", "2022-12"]
    vals = c.sine_series(months, 48, "2022-10")
    assert len(vals) == 3
    assert vals[0] == -1.0


def test_describe_current_structure():
    # 用自訂週期避免綁定預設參數：48月循環、谷底2022-10 → 2024-10 為半週期頂部
    cyc = {"key": "test", "name": "測試週期", "period_months": 48, "trough_ym": "2022-10"}
    d = c.describe_current("2024-10", cyc)
    assert d["stage"] == "頂部"
    assert d["key"] == "test"
    assert -1.0 <= d["value"] <= 1.0
