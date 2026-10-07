"""ma_monitor 關鍵邏輯測試。

跑法：python3 -m pytest scripts/tests/test_ma_monitor.py -v
"""
import datetime
import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

_MM_PATH = Path(__file__).resolve().parents[1] / "ma_monitor.py"


@pytest.fixture(scope="module")
def mm():
    spec = importlib.util.spec_from_file_location("ma_monitor", _MM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tw_holidays_match_open_data(mm):
    """TW_HOLIDAYS 必須等於人事行政總處行事曆的「週間休市日」。

    防隔年手改又抄錯 —— 曾把 2025 春節(1/26-1/30)誤植為 2026，並漏掉
    真正的 2026 農曆春節(2/16-2/20)。無網路時自動 skip。
    """
    url = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/2026.json"
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=15).read())
    except Exception as e:  # noqa: BLE001 - 任何網路問題都只 skip
        pytest.skip(f"取得行事曆開放資料失敗：{e}")

    expected = {
        x["date"] for x in data
        if x.get("isHoliday")
        and x["date"].startswith("2026")
        and datetime.date.fromisoformat(x["date"]).weekday() < 5
    }
    mine = {d.replace("-", "") for d in mm.TW_HOLIDAYS}
    assert mine == expected, (
        f"與開放資料不符  漏列={sorted(expected - mine)}  多列={sorted(mine - expected)}"
    )


@pytest.mark.parametrize(
    "bars, today, expected",
    [
        # gateway 有補今日（yfinance 合成K）：今日那根必須被排除
        ([{"date": "2026-10-06", "close": 625}, {"date": "2026-10-07", "close": 656}],
         "2026-10-07", [625.0]),
        # gateway 沒補今日（fail-open）：最後一個真實交易日必須保留，不可整根丟掉
        ([{"date": "2026-10-05", "close": 626}, {"date": "2026-10-06", "close": 625}],
         "2026-10-07", [626.0, 625.0]),
        # 完全沒資料
        ([], "2026-10-07", []),
    ],
)
def test_closed_closes(mm, bars, today, expected):
    assert mm.closed_closes(bars, today) == expected


def test_load_watchlist_from_shared_config(mm):
    """load_watchlist() 從共用設定檔讀出 {code: (exchange, name)}。"""
    wl = mm.load_watchlist()
    assert wl, "共用設定檔讀不到或為空"
    for code, (ex, name) in wl.items():
        assert len(code) == 4 and code.isdigit()
        assert ex in ("TSE", "OTC")
        assert name


def test_load_watchlist_missing_file_returns_empty(mm, monkeypatch, tmp_path):
    """設定檔不存在時回空 dict，不可拋例外。"""
    monkeypatch.setattr(mm, "WATCHLIST_FILE", tmp_path / "nope.json")
    assert mm.load_watchlist() == {}


def test_load_watchlist_bad_json_returns_empty(mm, monkeypatch, tmp_path):
    """設定檔格式錯誤時回空 dict，不可拋例外。"""
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(mm, "WATCHLIST_FILE", p)
    assert mm.load_watchlist() == {}
