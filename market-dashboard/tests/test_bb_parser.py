"""美銀牛熊指標解析與防護測試。

這些案例全部來自實際抓到的標題與 code review 指出的誤判情境。
解析錯誤的代價很高：錯誤讀數會寫進 bb_cache.json 並可能觸發九宮格推播，
所以「抓不到」比「抓錯」安全。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate


@pytest.mark.parametrize("text,expected,note", [
    # ── 應該抓到目標值 ──
    ("BofA Bull & Bear Indicator slides to 7.4 from 8.1", 7.4, "英文 slides to"),
    ("Bull & Bear Indicator fell from 8.1 to 7.4",        7.4, "from X to Y 取 Y"),
    ("Bull & Bear Indicator hits 8.5",                    8.5, "英文 hits"),
    ("Bull & Bear Indicator stands at 6.0",               6.0, "英文 stands at"),
    ("美銀牛熊指標降至 7.4 結束賣出訊號",                          7.4, "繁中 降至"),
    ("美银：牛熊指标回落至7.4",                                 7.4, "簡中 回落至"),
    ("美银牛熊指标从8.1降到7.4",                                7.4, "從X降到Y 取 Y"),
    ("美银牛熊指标已经由前一周的8.1明显下降至7.4",                    7.4, "長句 下降至"),
    ("美银牛熊指标从9.9升至10.0",                               10.0, "10.0 不可截成 0.0"),
    ("美银牛熊指标冲上9.6创历史新高",                             9.6, "簡中 冲上"),
])
def test_extracts_target_value(text, expected, note):
    assert generate._parse_bb_value(text) == expected, note


@pytest.mark.parametrize("text,expected,note", [
    ("美银牛熊指标跌破8.0，卖出信号结束", None, "8.0 是決策門檻不是讀數"),
    ("Bull & Bear Indicator falls below 8.0", None, "below 門檻不是讀數"),
    ("Bull market vs bear market: S&P 500 climbs 3.2% this year", None, "無關漲跌幅"),
    ("美银牛熊指标创2021年来新高！Hartnett坚持撤退", None, "年份不是讀數"),
    ("nothing here", None, "無關文字"),
])
def test_ignores_unrelated_numbers(text, expected, note):
    assert generate._parse_bb_value(text) is expected, note


def test_value_range_enforced():
    """11.5 超出 0–10，不可當成讀數。"""
    assert generate._parse_bb_value("Bull & Bear Indicator slides to 11.5") is None


def _with_cache(tmp_path, monkeypatch, history, found):
    import json
    cache = tmp_path / "bb.json"
    cache.write_text(json.dumps({"history": history}))
    monkeypatch.setattr(generate, "BB_CACHE", cache)
    monkeypatch.setattr(generate, "_fetch_bb_from_rss", lambda url: found)
    result = generate.fetch_bofa_bull_bear()          # 先執行才會寫檔
    return json.loads(cache.read_text())["history"], result


def test_rejects_future_date(tmp_path, monkeypatch):
    """時區標錯產生的未來日期不可寫入。"""
    hist, result = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}],
        [("2999-01-01", 7.0)])
    assert len(hist) == 1
    assert result["value"] == 7.4


def test_rejects_older_date(tmp_path, monkeypatch):
    """比快取末筆更舊的日期不可寫入（RSS 會回舊文）。"""
    hist, _ = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}],
        [("2026-09-01", 6.8)])
    assert len(hist) == 1


def test_accepts_newer_date(tmp_path, monkeypatch):
    hist, result = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}],
        [("2026-10-23", 7.2)])
    assert len(hist) == 2
    assert result["value"] == 7.2


def test_duplicate_within_window_not_added(tmp_path, monkeypatch):
    """3 天後的同值轉載不該製造假更新點。"""
    hist, result = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}],
        [("2026-10-19", 7.4)])
    assert len(hist) == 1
    assert result["date"] == "2026-10-16"


def test_same_value_after_window_is_new_week(tmp_path, monkeypatch):
    """隔 7 天以上仍是同值 → 是新一週的讀數，要記錄（快取裡 7.4×2、8.8×2 都是這種）。"""
    hist, _ = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}],
        [("2026-10-23", 7.4)])
    assert len(hist) == 2


def test_days_old_reported(tmp_path, monkeypatch):
    _, result = _with_cache(
        tmp_path, monkeypatch,
        [{"date": "2026-10-16", "value": 7.4}], [])
    assert isinstance(result["days_old"], int)
    assert result["days_old"] >= 0


def test_unparseable_date_does_not_crash(tmp_path, monkeypatch):
    """日期為 null 時不可讓整個生成流程爆掉。"""
    _, result = _with_cache(tmp_path, monkeypatch, [{"date": None, "value": 7.4}], [])
    assert result["days_old"] is None


def test_multiple_null_dates_do_not_crash_sort(tmp_path, monkeypatch):
    """兩筆以上 null 日期時，排序會比較 None 與 str → TypeError。"""
    _, result = _with_cache(
        tmp_path, monkeypatch,
        [{"date": None, "value": 7.4}, {"date": "2026-10-16", "value": 7.0}], [])
    assert result["value"] == 7.0        # null 那天排到最前面


def test_corrupted_cache_falls_back_to_empty(tmp_path, monkeypatch):
    """半截 JSON 不該讓整份儀表板中止。"""
    import json
    cache = tmp_path / "bb.json"
    cache.write_text('{"history": [{"date"')
    monkeypatch.setattr(generate, "BB_CACHE", cache)
    monkeypatch.setattr(generate, "_fetch_bb_from_rss", lambda url: [])
    result = generate.fetch_bofa_bull_bear()
    assert result["value"] is None


def test_future_dated_cache_not_treated_as_fresh(tmp_path, monkeypatch):
    """快取日期在未來時 days_old 為負，不可判定為新鮮。"""
    _, result = _with_cache(
        tmp_path, monkeypatch, [{"date": "2999-01-01", "value": 7.4}], [])
    assert result["days_old"] is not None and result["days_old"] < 0
