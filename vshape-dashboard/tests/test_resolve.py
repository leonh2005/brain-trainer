"""resolve_code 的單元測試（不碰網路：全部 monkeypatch）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as vd  # noqa: E402
import pytest  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(vd, "_name_map", {"ts": 0.0, "data": {}})
    yield


def _search(monkeypatch, results, snapshot=None):
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/stock_search"):
            return _Resp({"ok": True, "results": results})
        if url.endswith("/market_snapshot"):
            return _Resp({"ok": True, "items": snapshot or []})
        raise AssertionError(url)
    monkeypatch.setattr(vd.requests, "get", fake_get)


def test_code_passthrough_and_normalisation(monkeypatch):
    _search(monkeypatch, [])
    assert vd.resolve_code("2330") == ("2330", None)
    assert vd.resolve_code("00631l") == ("00631L", None)


def test_exact_chinese_name(monkeypatch):
    _search(monkeypatch, [{"code": "2330", "name": "台積電"}])
    assert vd.resolve_code("台積電") == ("2330", None)


def test_tai_variant_normalised(monkeypatch):
    _search(monkeypatch, [{"code": "2330", "name": "台積電"}])
    assert vd.resolve_code("臺積電") == ("2330", None)


def test_six_digit_etf(monkeypatch):
    _search(monkeypatch, [{"code": "006208", "name": "富邦台50"}])
    assert vd.resolve_code("富邦台50") == ("006208", None)


def test_ambiguous_returns_candidates(monkeypatch):
    _search(monkeypatch, [{"code": "00401A", "name": "主動摩根台灣鑫收"},
                          {"code": "00405A", "name": "主動富邦台灣龍耀"}])
    code, cands = vd.resolve_code("台灣")
    assert code is None and len(cands) == 2


def test_not_found(monkeypatch):
    _search(monkeypatch, [], snapshot=[])
    assert vd.resolve_code("不存在的股票") == (None, [])


def test_gateway_down_is_not_not_found(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("gateway down")
    monkeypatch.setattr(vd.requests, "get", boom)
    with pytest.raises(vd.LookupUnavailable):
        vd.resolve_code("台積電")


def test_partial_snapshot_does_not_overwrite_cache(monkeypatch):
    vd._name_map["data"] = {"台積電": ["2330"]}
    vd._name_map["ts"] = 0.0

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/stock_search"):
            return _Resp({"ok": True, "results": []})
        return _Resp({"ok": True, "partial": True,
                      "items": [{"code": "1234", "name": "別家"}]})
    monkeypatch.setattr(vd.requests, "get", fake_get)

    assert vd._name_map_refresh() == {"台積電": ["2330"]}


def test_name_map_fallback(monkeypatch):
    _search(monkeypatch, [], snapshot=[{"code": "0050", "name": "元大台灣50"}])
    assert vd.resolve_code("元大台灣50") == ("0050", None)
