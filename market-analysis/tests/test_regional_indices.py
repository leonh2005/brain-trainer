"""test_regional_indices: 日韓指數的資料新鮮度標示。

對應 2026-11-03 修正：休市日不能把舊收盤價當今日即時價，
也不能把「停滯」一律標成「收盤」（日經午休每天會停一小時）。
"""

import types
from datetime import UTC, datetime, timedelta

import app
import pandas as pd

# 固定基準：2026-11-03（週二）東京 10:00，讓 _freshness 的斷言不依賴執行時刻
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=app.EXCHANGE_TZ)


def _quote(ts, price=100.0, prev=99.0):
    return {"price": price, "prev_close": prev, "ts": ts}


def _fake_ticker(index, closes):
    frame = pd.DataFrame({"Close": closes}, index=index)
    return types.SimpleNamespace(history=lambda **_kw: frame)


# ── _freshness：三種狀態 ────────────────────────────────────────────

def test_freshness_holiday_marks_not_today():
    """休市日：新浪回上一個交易日收盤 → is_today=False、is_stale=True。"""
    f = app._freshness(NOW - timedelta(hours=18, minutes=30), NOW)  # 11-02 15:30 JST

    assert f["is_today"] is False
    assert f["is_stale"] is True
    assert f["as_of"] == "2026-11-02"
    assert f["as_of_time"] == "15:30"


def test_freshness_live_has_no_stale_flag():
    f = app._freshness(NOW - timedelta(seconds=10), NOW)

    assert f["is_today"] is True
    assert f["is_stale"] is False


def test_freshness_lunch_break_is_stale_but_today():
    """午休：tick 停 30 分鐘但仍是今天 → 前端標「截至 HH:MM」。"""
    f = app._freshness(NOW - timedelta(minutes=30), NOW)

    assert f["is_today"] is True
    assert f["is_stale"] is True


# ── _regional_indices ───────────────────────────────────────────────

def test_holiday_quote_marks_not_today(monkeypatch):
    ts = datetime.now(app.EXCHANGE_TZ) - timedelta(days=1)
    monkeypatch.setattr(app, "_sina_index_quote", lambda _s: _quote(ts))

    r = app._regional_indices()[0]

    assert r["is_today"] is False
    assert r["is_stale"] is True


def test_sina_failure_omits_index(monkeypatch):
    """新浪取不到 → 該指數缺席，不顯示可能錯誤的數字。"""
    monkeypatch.setattr(app, "_sina_index_quote", lambda _s: None)

    assert app._regional_indices() == []


# ── _fetch_regional_intraday ────────────────────────────────────────

def test_intraday_curve_stuck_on_previous_day_is_not_today(monkeypatch):
    """曲線還停在昨天、報價已是今天 → 不能標成「今天未停滯」。"""
    yesterday = datetime.now(app.EXCHANGE_TZ) - timedelta(days=1)
    index = pd.DatetimeIndex([yesterday.replace(hour=9, minute=0),
                              yesterday.replace(hour=9, minute=1)])
    monkeypatch.setattr("yfinance.Ticker", lambda _t: _fake_ticker(index, [100.0, 101.0]))
    monkeypatch.setattr(app, "_sina_index_quote", lambda _s: _quote(datetime.now(UTC)))

    r = app._fetch_regional_intraday(force=True)["regional"][0]

    assert r["is_today"] is False
    assert r["is_stale"] is True
    assert len(r["points"]) == 2          # 跨日不得補點
    assert r["prev_close"] is None        # 跨日時新浪昨收屬於曲線當天，不能拿來畫參考線


def test_intraday_appends_same_day_sina_tick(monkeypatch):
    """同日補點：曲線最後一根早於新浪報價 → 追加一點，時間戳改用新浪。"""
    today = datetime.now(app.EXCHANGE_TZ)
    index = pd.DatetimeIndex([today.replace(hour=9, minute=0), today.replace(hour=9, minute=1)])
    monkeypatch.setattr("yfinance.Ticker", lambda _t: _fake_ticker(index, [100.0, 101.0]))
    sina_ts = today.replace(hour=9, minute=2)
    monkeypatch.setattr(app, "_sina_index_quote",
                        lambda _s: _quote(sina_ts, price=102.0, prev=99.0))

    r = app._fetch_regional_intraday(force=True)["regional"][0]

    assert r["points"][-1] == {"t": "09:02", "price": 102.0}
    assert r["prev_close"] == 99.0
    assert r["as_of_time"] == "09:02"     # 補點成功 → 採用新浪時間戳
