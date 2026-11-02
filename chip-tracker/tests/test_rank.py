# -*- coding: utf-8 -*-
"""rank.py：向 shioaji-gateway 取行情排行的純邏輯測試（不碰網路）。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import rank


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_rank_sends_kind_and_count(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        return _Resp({"ok": True, "items": [{"code": "2330", "name": "台積電", "pct": 2.5}]})

    monkeypatch.setattr(rank.requests, "get", fake_get)
    items = rank.fetch_rank("volume", count=20)

    assert items == [{"code": "2330", "name": "台積電", "pct": 2.5}]
    assert seen["params"] == {"type": "volume", "count": 20, "ascending": "true"}
    assert seen["url"].endswith("/scanner")


def test_fetch_rank_rejects_unknown_kind():
    with pytest.raises(ValueError):
        rank.fetch_rank("turnover")


def test_fetch_rank_raises_when_gateway_reports_error(monkeypatch):
    monkeypatch.setattr(rank.requests, "get", lambda *a, **k: _Resp({"ok": False, "error": "掃描失敗"}))
    with pytest.raises(RuntimeError, match="掃描失敗"):
        rank.fetch_rank("pct")


def test_fetch_rank_empty_list_is_valid(monkeypatch):
    """盤前/假日 gateway 回空清單是合法的「沒有資料」。"""
    monkeypatch.setattr(rank.requests, "get", lambda *a, **k: _Resp({"ok": True, "items": []}))
    assert rank.fetch_rank("amount") == []


def test_fetch_rank_missing_items_key_raises(monkeypatch):
    """回應缺少 items 欄位代表上游壞掉，不能吞成「沒有資料」。"""
    monkeypatch.setattr(rank.requests, "get", lambda *a, **k: _Resp({"ok": True}))
    with pytest.raises(RuntimeError, match="items"):
        rank.fetch_rank("amount")
