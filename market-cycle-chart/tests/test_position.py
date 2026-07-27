# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import current_position as cp
import econ_cycles as ec
import wave_scripts as ws


def test_wave_status_last_passed_wave():
    v1 = ws.get_script("v1")
    # 2026-07 應已過所有 anchor(最後一個是 2026-02 基準高)
    st = cp.wave_status(v1, "2026-07", 43634)
    assert st["last_passed_wave"] == "基準高"
    assert st["last_passed_date"] == "2026-02"


def test_wave_status_target_gap():
    v1 = ws.get_script("v1")
    st = cp.wave_status(v1, "2026-07", 43634)
    # 目標 47000 距 43634 約 +7.7%
    t47 = next(t for t in st["targets"] if t["price"] == 47000)
    assert 7.0 < t47["gap_pct"] < 8.5


def test_wave_status_before_any_anchor():
    v1 = ws.get_script("v1")
    st = cp.wave_status(v1, "2020-01", 12000)
    assert st["last_passed_wave"] is None


def test_wave_status_retracement_gap_negative():
    v1 = ws.get_script("v1")
    st = cp.wave_status(v1, "2026-07", 43634)
    # 回檔買點 36000-38000 在現價下方，gap 應為負
    r = st["retracements"][0]
    assert r["low_gap_pct"] < 0 and r["high_gap_pct"] < 0


def test_cycle_stages_structure():
    stages = cp.cycle_stages("2024-10")
    assert len(stages["market"]) == 3
    assert len(stages["econ"]) == 2
    # 每條週期都應有合法段位
    valid = {"底部", "上升段", "頂部", "下降段"}
    for s in stages["market"] + stages["econ"]:
        assert s["stage"] in valid


def test_econ_series_length():
    months = ["2023-01", "2023-02", "2023-03"]
    vals = ec.series(months, ec.DEFAULT_ECON_CYCLES[0])
    assert len(vals) == 3
    assert vals[0] == -1.0  # 基欽谷底錨點 2023-01
