"""intraday_monitor 的 gateway HTTP 客戶端測試（不碰網路、不登入 Shioaji）。

對應 2026-11-12 改動：monitor 不再自己登入，tick 改由 gateway 中繼。
"""
from datetime import datetime

import intraday_monitor as m
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(m, '_universe', {})
    monkeypatch.setattr(m, '_universe_max_chg', {})
    monkeypatch.setattr(m, '_universe_joined', {})
    monkeypatch.setattr(m, '_universe_missed', {})
    monkeypatch.setattr(m, '_last_bar_signals', {})
    monkeypatch.setattr(m, '_tick_store', {})
    monkeypatch.setattr(m, '_tick_cursor', {})
    monkeypatch.setattr(m, '_gateway_boot_id', 'boot-1')
    monkeypatch.setattr(m, '_snapshot_health', {'fail_streak': 0})
    yield


# ── tick 解碼與回補 ─────────────────────────────────────────────

def test_decode_ticks_types():
    rows = m._decode_ticks([["2026-11-12T09:00:01.000000", 100.5, 3, 1]])
    assert rows[0][0] == pd.Timestamp("2026-11-12T09:00:01")
    assert rows[0][1:] == (100.5, 3, 1)


def test_merge_backfill_only_newer():
    m._tick_store["2330"] = [(pd.Timestamp("2026-11-12T09:00:02"), 101.0, 2, 1)]
    added = m._merge_backfill("2330", [
        (pd.Timestamp("2026-11-12T09:00:01"), 100.0, 1, 1),
        (pd.Timestamp("2026-11-12T09:00:03"), 102.0, 3, 1),
    ])
    assert added == 1
    assert [r[1] for r in m._tick_store["2330"]] == [101.0, 102.0]


# ── snapshot ───────────────────────────────────────────────────

def test_get_snapshot_parses_extended_fields(monkeypatch):
    monkeypatch.setattr(m, '_get_json', lambda path, params, timeout: {
        'ok': True,
        'data': {'2330': {'name': '台積電', 'close': 100.0, 'change_rate': 1.5,
                          'total_volume': 10, 'yesterday_volume': 20,
                          'buy_volume': 3, 'sell_volume': 4}},
    })
    snap = m.get_snapshot(['2330'])
    assert snap['2330'] == {'name': '台積電', 'close': 100.0, 'chg_pct': 1.5,
                            'total_vol': 10, 'yday_vol': 20,
                            'buy_volume': 3, 'sell_volume': 4}
    assert m._snapshot_health['fail_streak'] == 0


def test_get_snapshot_failure_increments_streak(monkeypatch):
    def boom(path, params, timeout):
        raise RuntimeError("payload: {'token': 'eyJabcdefgh.abcdefgh.abcdefgh'}")
    monkeypatch.setattr(m, '_get_json', boom)
    assert m.get_snapshot(['2330']) == {}
    assert m._snapshot_health['fail_streak'] == 1


# ── 訂閱 ───────────────────────────────────────────────────────

def _subscribe_payload(**over):
    info = {
        'subscribed': True, 'name': '元大台灣50正2', 'reference': 100.0,
        'limit_up': 120.0, 'limit_down': 80.0, 'head_seq': 5, 'resume_seq': 3,
        'backfill': [["2026-11-12T09:00:01.000000", 99.0, 1, 1]],
    }
    info.update(over)
    return {'ok': True, 'boot_id': 'boot-1', 'generation': 7, 'data': {'00631L': info}}


def test_subscribe_sets_metadata_and_cursor(monkeypatch):
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: _subscribe_payload())
    ok = m._subscribe('00631L', '00631L', datetime(2026, 11, 12, 9, 30))
    assert ok is True
    assert m._universe['00631L'] == '元大台灣50正2'
    assert m._tick_cursor['00631L'] == 3
    # 20% 漲跌幅 → 7% × 2 = 14%
    assert m._universe_max_chg['00631L'] == pytest.approx(14.0)
    assert len(m._tick_store['00631L']) == 1


def test_subscribe_keep_ticks_merges(monkeypatch):
    m._tick_store['00631L'] = [(pd.Timestamp("2026-11-12T09:00:00"), 98.0, 1, 1)]
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: _subscribe_payload())
    m._subscribe('00631L', '00631L', datetime(2026, 11, 12, 9, 30), keep_ticks=True)
    assert [r[1] for r in m._tick_store['00631L']] == [98.0, 99.0]


def test_subscribe_failure_returns_false(monkeypatch):
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {
        'ok': True, 'boot_id': 'b', 'data': {'2330': {'subscribed': False, 'error': 'contract not found'}}})
    assert m._subscribe('2330', '台積電', datetime(2026, 11, 12, 9, 30)) is False
    assert '2330' not in m._universe


# ── 輪詢 ───────────────────────────────────────────────────────

def test_poll_ticks_updates_store_and_cursor(monkeypatch):
    m._universe['2330'] = '台積電'
    m._tick_cursor['2330'] = 1
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {
        'ok': True, 'boot_id': 'boot-1',
        'data': {'2330': {'subscribed': True, 'seq': 3, 'first_seq': 1,
                          'ticks': [["2026-11-12T09:00:02.000000", 101.0, 2, 1],
                                    ["2026-11-12T09:00:03.000000", 102.0, 3, 1]]}},
    })
    assert m.poll_ticks() == []
    assert m._tick_cursor['2330'] == 3
    assert [r[1] for r in m._tick_store['2330']] == [101.0, 102.0]


def test_poll_ticks_boot_id_change_resubscribes_all(monkeypatch):
    m._universe.update({'2330': '台積電', '2454': '聯發科'})
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {'ok': True, 'boot_id': 'boot-2', 'data': {}})
    assert sorted(m.poll_ticks()) == ['2330', '2454']
    assert m._gateway_boot_id == 'boot-2'
    assert m._tick_cursor == {}


def test_poll_ticks_reports_unsubscribed_and_truncated(monkeypatch):
    m._universe.update({'2330': '台積電', '2454': '聯發科'})
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {
        'ok': True, 'boot_id': 'boot-1',
        'data': {'2330': {'subscribed': False},
                 '2454': {'subscribed': True, 'seq': 9, 'first_seq': 9, 'ticks': [], 'truncated': True}},
    })
    assert sorted(m.poll_ticks()) == ['2330', '2454']


# ── 均量 ───────────────────────────────────────────────────────

def test_get_avg5_vol_filters_today(monkeypatch):
    today = m.date.today().isoformat()
    bars = [{'date': f'2026-11-0{i}', 'volume': i * 100} for i in range(1, 6)]
    bars.append({'date': today, 'volume': 99999})
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {'ok': True, 'bars': bars})
    cache = {}
    assert m.get_avg5_vol('2330', cache) == 300     # (100+200+300+400+500)/5
    assert cache['2330'] == 300


def test_get_avg5_vol_insufficient_bars(monkeypatch):
    monkeypatch.setattr(m, '_get_json', lambda p, q, t: {
        'ok': True, 'bars': [{'date': '2026-11-10', 'volume': 100}]})
    assert m.get_avg5_vol('2330', {}) == 0
