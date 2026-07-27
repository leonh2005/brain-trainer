# -*- coding: utf-8 -*-
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import taiex_data


def test_health():
    assert app.app.test_client().get("/api/health").get_json() == {"status": "ok"}


def test_bad_script_id_returns_400():
    r = app.app.test_client().get("/api/chart-data?script=nope")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_chart_data_empty_monthly_returns_502(monkeypatch):
    # reviewer 指出的邊界：resample 後月線為空應明確報錯而非 500 IndexError
    monkeypatch.setattr(taiex_data, "get_daily", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(taiex_data, "get_monthly", lambda *a, **k: pd.DataFrame())
    r = app.app.test_client().get("/api/chart-data?script=v1")
    assert r.status_code == 502
    assert "月線資料為空" in r.get_json()["error"]


def test_month_range():
    assert app.month_range("2026-01", "2026-03") == ["2026-01", "2026-02", "2026-03"]
    assert app.month_range("2025-12", "2026-01") == ["2025-12", "2026-01"]
