# -*- coding: utf-8 -*-
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taiex_data as td


def _fake_rows():
    # 兩個月的假日線，用來測 resample 與 normalize
    return [
        {"date": "2026-01-05", "open": 100, "max": 110, "min": 95, "close": 105, "Trading_Volume": 1000},
        {"date": "2026-01-20", "open": 105, "max": 120, "min": 100, "close": 115, "Trading_Volume": 2000},
        {"date": "2026-02-03", "open": 115, "max": 125, "min": 112, "close": 120, "Trading_Volume": 1500},
        {"date": "2026-02-25", "open": 120, "max": 130, "min": 118, "close": 128, "Trading_Volume": 2500},
    ]


def test_normalize_renames_columns():
    df = td._normalize(_fake_rows())
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df["high"].iloc[0] == 110
    assert df["low"].iloc[0] == 95
    assert df["volume"].iloc[1] == 2000


def test_normalize_sorts_by_date():
    rows = list(reversed(_fake_rows()))
    df = td._normalize(rows)
    assert df["date"].is_monotonic_increasing


def test_get_monthly_resamples_ohlc():
    daily = td._normalize(_fake_rows())
    m = td.get_monthly(daily)
    assert len(m) == 2  # 一月、二月
    jan = m.iloc[0]
    assert jan["open"] == 100   # 當月第一筆開盤
    assert jan["high"] == 120   # 當月最高
    assert jan["low"] == 95     # 當月最低
    assert jan["close"] == 115  # 當月最後收盤
    assert jan["volume"] == 3000  # 當月成交量加總


def test_add_moving_averages():
    df = td._normalize(_fake_rows())
    out = td.add_moving_averages(df, windows=(2,))
    # ma2 第二筆 = (105+115)/2 = 110
    assert out["ma2"].iloc[1] == 110
    # min_periods=1，第一筆等於自身收盤
    assert out["ma2"].iloc[0] == 105


def test_get_daily_falls_back_to_cache(monkeypatch, tmp_path):
    """FinMind 失敗時應回退舊快取而非拋錯。"""
    cache = {"fetched_date": "2020-01-01", "rows": _fake_rows()}
    cache_file = tmp_path / "taiex_daily.json"
    import json
    cache_file.write_text(json.dumps(cache))
    monkeypatch.setattr(td, "_CACHE_PATH", str(cache_file))

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(td, "_fm_get_taiex", boom)
    df = td.get_daily()  # 不應拋錯
    assert len(df) == 4


def test_get_daily_raises_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "_CACHE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(td, "_fm_get_taiex", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(td.TaiexDataError):
        td.get_daily()
