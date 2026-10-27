"""command-center 路線規劃的單元測試。

只測不碰網路的純邏輯：時間解析、運具優先度、班次排序。
所有 HTTP 與地理編碼呼叫都不在此測試範圍。
"""
from datetime import datetime

import pytest
import route_planner as rp

# ── _mode_rank：運具優先度（台鐵 > 捷運 > 公車）──────────────

@pytest.mark.parametrize(
    "mode,expected",
    [
        ("REGIONAL_RAIL", 0),
        ("HIGHSPEED_RAIL", 0),
        ("SUBWAY", 1),
        ("BUS", 2),
    ],
)
def test_mode_rank_follows_priority_table(mode, expected):
    """台鐵類 0、捷運類 1、公車類 2。"""
    assert rp._mode_rank([mode]) == expected


def test_mode_rank_takes_best_of_multiple_modes():
    """一段行程混合多種運具時，取最小（最優先）的那個。"""
    assert rp._mode_rank(["BUS", "REGIONAL_RAIL"]) == 0


def test_mode_rank_unknown_mode_falls_back():
    """未知運具給 fallback 排名，不會爆掉。"""
    assert rp._mode_rank(["HOVERCRAFT"]) == rp._MODE_FALLBACK_RANK


def test_mode_rank_empty_list_falls_back():
    """空清單也要回 fallback，不是例外。"""
    assert rp._mode_rank([]) == rp._MODE_FALLBACK_RANK


# ── 時間解析 ────────────────────────────────────────────

def test_hm_formats_iso_to_hhmm_in_taipei():
    """UTC ISO 時間要轉成台北時區的 HH:MM。"""
    # 2026-10-27T01:30:00Z = 台北 09:30
    assert rp._hm("2026-10-27T01:30:00Z") == "09:30"


def test_hm_returns_empty_for_invalid():
    """無法解析時回空字串，不拋例外。"""
    assert rp._hm("not-a-time") == ""
    assert rp._hm("") == ""


def test_hmm_returns_minutes_since_midnight():
    """回傳當日 00:00 起算的分鐘數（台北時區）。"""
    # 台北 09:30 → 570 分
    assert rp._hmm("2026-10-27T01:30:00Z") == 570


def test_hmm_returns_zero_for_invalid():
    """無法解析時回 0，不拋例外。"""
    assert rp._hmm("bad") == 0
    assert rp._hmm(None) == 0


def test_iso_time_normalizes_single_digit_hour():
    """9:05 要正規化成 09:05。"""
    assert rp._iso_time("9:05") == "09:05"


@pytest.mark.parametrize("bad", ["25:00", "12:60", "abc", "12-30", ""])
def test_iso_time_rejects_invalid(bad):
    """格式錯誤或超範圍要拋 ValueError。"""
    with pytest.raises(ValueError):
        rp._iso_time(bad)


def test_iso_day_uses_given_date():
    """有指定日期就照用，不自動推算。"""
    assert rp._iso_day("2026-10-27", "09:00") == "2026-10-27"


def test_iso_day_rejects_bad_format():
    """日期格式錯誤要拋 ValueError。"""
    with pytest.raises(ValueError):
        rp._iso_day("2026/10/27", "09:00")


def test_iso_day_rolls_to_tomorrow_when_time_passed(monkeypatch):
    """沒指定日期且目標時刻已過 → 應該是明天。"""
    fixed = datetime(2026, 10, 27, 23, 0, tzinfo=rp.TPE)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(rp, "datetime", FakeDatetime)
    assert rp._iso_day(None, "08:00") == "2026-10-28"


def test_iso_day_keeps_today_when_time_not_passed(monkeypatch):
    """沒指定日期且目標時刻還沒到 → 還是今天。"""
    fixed = datetime(2026, 10, 27, 6, 0, tzinfo=rp.TPE)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(rp, "datetime", FakeDatetime)
    assert rp._iso_day(None, "08:00") == "2026-10-27"


# ── _rank：班次排序（最核心的邏輯）──────────────────────

def _route(mode_rank, start_min, end_min, walk_min, total_min):
    return {
        "mode_rank": mode_rank,
        "start_min": start_min,
        "end_min": end_min,
        "walk_min": walk_min,
        "min": total_min,
    }


def test_rank_returns_only_feasible_routes():
    """抵達時間晚於目標的班次要被剔除。"""
    routes = [
        _route(0, 0, 100, 5, 100),   # 目標 90 前到不了
        _route(0, 0, 80, 5, 80),     # 可以
    ]
    picked, feasible = rp._rank(routes, target_min=90)
    assert feasible is True
    assert [r["end_min"] for r in picked] == [80]


def test_rank_prefers_rail_over_bus_even_if_later():
    """有台鐵就排前面，即使公車更早到（運具優先度是硬性的）。"""
    routes = [
        _route(2, 0, 60, 0, 60),   # 公車，早到
        _route(0, 0, 85, 0, 85),   # 台鐵，較晚到但運具優先
    ]
    picked, _ = rp._rank(routes, target_min=90)
    assert picked[0]["mode_rank"] == 0


def test_rank_prefers_latest_arrival_within_same_mode():
    """同一運具等級內，選「不遲到的最晚抵達」避免在目的地空等。"""
    routes = [
        _route(0, 0, 60, 0, 60),   # 早到 30 分
        _route(0, 0, 88, 0, 88),   # 接近目標 90
    ]
    picked, _ = rp._rank(routes, target_min=90)
    assert picked[0]["end_min"] == 88


def test_rank_breaks_tie_by_less_walking():
    """同運具、同抵達時間時，步行少的優先。"""
    routes = [
        _route(0, 0, 80, 12, 80),
        _route(0, 0, 80, 3, 80),
    ]
    picked, _ = rp._rank(routes, target_min=90)
    assert picked[0]["walk_min"] == 3


def test_rank_breaks_tie_by_shorter_total_time():
    """同運具、同抵達、同步行時，總時間短的優先。"""
    routes = [
        _route(0, 0, 80, 3, 95),
        _route(0, 0, 80, 3, 70),
    ]
    picked, _ = rp._rank(routes, target_min=90)
    assert picked[0]["min"] == 70


def test_rank_infeasible_falls_back_to_earliest_arrival():
    """全部趕不上時，改求最快抵達，且 feasible=False。"""
    routes = [
        _route(0, 0, 120, 0, 120),
        _route(2, 0, 100, 0, 100),
    ]
    picked, feasible = rp._rank(routes, target_min=90)
    assert feasible is False
    assert picked[0]["end_min"] == 100


def test_rank_filters_departed_routes_when_now_given():
    """查今天時要濾掉已經開走的班次。"""
    routes = [
        _route(0, 30, 80, 0, 50),   # 30 分出發，已過（now=60）
        _route(0, 65, 85, 0, 20),   # 還來得及
    ]
    picked, _ = rp._rank(routes, target_min=90, now_min=60)
    assert [r["start_min"] for r in picked] == [65]


def test_rank_keeps_all_when_every_route_already_departed():
    """若所有班次都已開走，不要回空清單（保留原清單讓上層判斷）。"""
    routes = [_route(0, 10, 50, 0, 40)]
    picked, _ = rp._rank(routes, target_min=90, now_min=60)
    assert len(picked) == 1


def test_rank_caps_at_max_routes():
    """回傳數量上限為 _MAX_ROUTES。"""
    routes = [_route(0, 0, 50 + i, 0, 50 + i) for i in range(10)]
    picked, _ = rp._rank(routes, target_min=100)
    assert len(picked) == rp._MAX_ROUTES


# ── 顯示名稱 ────────────────────────────────────────────

def test_name_translates_placeholder_endpoints():
    """START/END 佔位名稱要翻成中文。"""
    assert rp._name({"name": "START"}) == "出發地"
    assert rp._name({"name": "END"}) == "目的地"


def test_name_keeps_real_station_names():
    """真實站名原樣保留。"""
    assert rp._name({"name": "台北車站"}) == "台北車站"


def test_line_label_extracts_train_number_from_route_id():
    """從 Transitous 的 routeId 取出班次號。"""
    leg = {"routeId": "tdx_TRA_1234_20261027"}
    assert rp._line_label(leg) == "1234"


def test_line_label_falls_back_to_short_name():
    """取不到班次號時退回 routeShortName。"""
    leg = {"routeId": "other", "routeShortName": "紅32"}
    assert rp._line_label(leg) == "紅32"


def test_line_label_returns_empty_when_nothing():
    """完全沒有資訊時回空字串，不拋例外。"""
    assert rp._line_label({}) == ""
