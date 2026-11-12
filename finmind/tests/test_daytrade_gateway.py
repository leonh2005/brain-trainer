"""daytrade_alert 改走 gateway 後的純邏輯測試（不碰網路）。"""
from datetime import date

import daytrade_alert as d
import pytest


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    monkeypatch.setattr(d, '_bars_cache', {})
    yield


def _snap(code, suffix='.TW', close=100.0, high=105.0, low=100.0,
          change_rate=2.0, total_volume=1000, name=None):
    return {'code': code, 'suffix': suffix, 'name': name or code,
            'close': close, 'high': high, 'low': low,
            'change_rate': change_rate, 'total_volume': total_volume}


def test_top_volume_filters_tse_only(monkeypatch):
    monkeypatch.setattr(d, '_get_json', lambda p, q, t: {'ok': True, 'items': [
        _snap('2330'), _snap('8069', suffix='.TWO'), _snap('00631L'),
    ]})
    top = d.get_top_volume_sj()
    assert [r['code'] for r in top] == ['2330']


def test_top_volume_filters_zero_volume(monkeypatch):
    monkeypatch.setattr(d, '_get_json', lambda p, q, t: {'ok': True, 'items': [
        _snap('2330', total_volume=0), _snap('2454', total_volume=500),
    ]})
    assert [r['code'] for r in d.get_top_volume_sj()] == ['2454']


def test_top_volume_computes_amplitude_and_close_pos(monkeypatch):
    monkeypatch.setattr(d, '_get_json', lambda p, q, t: {'ok': True, 'items': [
        _snap('2330', close=102.0, high=104.0, low=100.0),
    ]})
    row = d.get_top_volume_sj()[0]
    assert row['amp_pct'] == pytest.approx(4.0)
    assert row['close_pos'] == pytest.approx(50.0)


def test_top_volume_gateway_failure_returns_empty(monkeypatch):
    def boom(p, q, t):
        raise RuntimeError('gateway down')
    monkeypatch.setattr(d, '_get_json', boom)
    assert d.get_top_volume_sj() == []


def test_daily_bars_filters_today_and_caches(monkeypatch):
    today = date.today().isoformat()
    calls = {'n': 0}

    def fake(p, q, t):
        calls['n'] += 1
        return {'ok': True, 'bars': [
            {'date': '2026-11-10', 'close': 100.0, 'volume': 100},
            {'date': today, 'close': 999.0, 'volume': 99999},
        ]}

    monkeypatch.setattr(d, '_get_json', fake)
    bars = d.get_daily_bars('2330')
    assert [b['date'] for b in bars] == ['2026-11-10']
    d.get_daily_bars('2330')
    assert calls['n'] == 1


def test_daily_bars_failure_not_cached(monkeypatch):
    calls = {'n': 0}

    def boom(p, q, t):
        calls['n'] += 1
        raise TimeoutError('gateway slow')

    monkeypatch.setattr(d, '_get_json', boom)
    assert d.get_daily_bars('2330') == []
    assert d.get_daily_bars('2330') == []
    assert calls['n'] == 2          # 失敗不進快取，下次仍會重試


def test_avg5_from_bars(monkeypatch):
    bars = [{'date': f'2026-11-0{i}', 'close': 100.0 + i, 'volume': i * 1000}
            for i in range(1, 6)]
    monkeypatch.setattr(d, 'get_daily_bars', lambda s: bars)
    assert d.get_sj_avg5('2330') == 3000


def test_streak_up_and_down(monkeypatch):
    up = [{'close': c, 'volume': 1} for c in (100, 101, 102, 103)]
    monkeypatch.setattr(d, 'get_daily_bars', lambda s: up)
    assert d.get_price_streak('2330') == 3

    down = [{'close': c, 'volume': 1} for c in (103, 102, 101, 100)]
    monkeypatch.setattr(d, 'get_daily_bars', lambda s: down)
    assert d.get_price_streak('2330') == -3


def test_streak_insufficient_data(monkeypatch):
    monkeypatch.setattr(d, 'get_daily_bars', lambda s: [{'close': 100, 'volume': 1}])
    assert d.get_price_streak('2330') == 0


def test_avg5_finmind_disabled_returns_zero():
    assert d.finmind is None
    assert d.get_avg5_finmind('2330') == 0
