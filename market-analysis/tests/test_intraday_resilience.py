"""test_intraday_resilience: gateway 短暫無回應時，日內波不能把上一筆成功資料丟掉。

對應 2026-11-11：Shioaji gateway 重登期間（30-60 秒）所有請求逾時，
5350 把失敗結果快取 55 秒並丟棄舊資料，前端就把整個圖表換成「暫無日內走勢資料」。
"""
import json
import time

import app
import pytest

POINTS = [
    {"t": "09:01", "price": 100.0},
    {"t": "09:02", "price": 101.0},
]

NEW_POINTS = [
    {"t": "09:03", "price": 102.0},
    {"t": "09:04", "price": 103.0},
]


class _FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


@pytest.fixture(autouse=True)
def _reset_cache():
    app._intraday_cache["data"] = None
    app._intraday_cache["ts"] = 0
    app._stocks_last_good.clear()
    yield
    app._intraday_cache["data"] = None
    app._intraday_cache["ts"] = 0
    app._stocks_last_good.clear()


def _serve(monkeypatch, payload):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(payload))


def _fail(monkeypatch, exc=None):
    def boom(*a, **k):
        raise exc or TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)


def test_failure_keeps_last_good_points(monkeypatch):
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    assert app._fetch_intraday(force=True)["ok"] is True

    _fail(monkeypatch)
    result = app._fetch_intraday(force=True)

    assert result["ok"] is True, "gateway 逾時不該讓日內波變成沒有資料"
    assert result["points"] == POINTS
    assert result["stale"] is True
    assert result["stale_age"] >= 0


def test_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    app._fetch_intraday(force=True)
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    # 沒有 force：失敗不該被快取住，下一次呼叫要立刻重試並拿到成功資料
    assert app._fetch_intraday()["ok"] is True
    assert calls["n"] == 1


def test_gateway_business_error_keeps_last_good(monkeypatch):
    """gateway 用 HTTP 200 回 {"ok": false} 也要沿用舊資料（比逾時更常見）。"""
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    app._fetch_intraday(force=True)

    _serve(monkeypatch, {"ok": False, "error": "contract not found"})
    result = app._fetch_intraday(force=True)
    assert result["ok"] is True
    assert result["points"] == POINTS
    assert result["stale"] is True


def test_success_is_not_marked_stale(monkeypatch):
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    result = app._fetch_intraday(force=True)
    assert "stale" not in result


def test_ttl_cache_hit_skips_network(monkeypatch):
    calls = {"n": 0}

    def counted(*a, **k):
        calls["n"] += 1
        return _FakeResp({"ok": True, "points": POINTS})

    monkeypatch.setattr("urllib.request.urlopen", counted)
    app._fetch_intraday(force=True)
    app._fetch_intraday()
    assert calls["n"] == 1


def test_stale_fallback_does_not_poison_cache(monkeypatch):
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    app._fetch_intraday(force=True)
    _fail(monkeypatch)
    app._fetch_intraday(force=True)  # 這次回 stale

    assert "stale" not in app._intraday_cache["data"], "stale 不能寫進快取，否則之後每一筆都標成過期"

    _serve(monkeypatch, {"ok": True, "points": NEW_POINTS})
    assert "stale" not in app._fetch_intraday(force=True)


def test_too_old_cache_is_not_served_as_current(monkeypatch):
    """gateway 可能一掛好幾小時；超過上限的舊資料不能冒充現況。"""
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    app._fetch_intraday(force=True)
    app._intraday_cache["ts"] = time.time() - app.INTRADAY_MAX_STALE_SEC - 1

    _fail(monkeypatch)
    assert app._fetch_intraday(force=True)["ok"] is False


def test_empty_points_do_not_overwrite_good_cache(monkeypatch):
    _serve(monkeypatch, {"ok": True, "points": POINTS})
    app._fetch_intraday(force=True)

    _serve(monkeypatch, {"ok": True, "points": []})
    result = app._fetch_intraday(force=True)
    assert result["ok"] is True
    assert result["points"] == POINTS, "盤前空回應不該把上一筆可用資料蓋掉"
    assert app._intraday_cache["data"]["points"] == POINTS


def test_no_data_at_all_still_reports_failure(monkeypatch):
    _fail(monkeypatch)
    result = app._fetch_intraday(force=True)
    assert result["ok"] is False
