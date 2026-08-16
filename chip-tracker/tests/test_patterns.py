# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import patterns


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_bullish_engulfing():
    rows = [_bar(12, 12.5, 9.5, 10), _bar(9.8, 13, 9.5, 12.8)]
    assert "多頭吞噬" in patterns.detect_candlestick(rows)


def test_bearish_engulfing():
    rows = [_bar(10, 12.5, 9.8, 12), _bar(12.2, 12.5, 9, 9.5)]
    assert "空頭吞噬" in patterns.detect_candlestick(rows)


def test_dark_cloud_cover():
    rows = [_bar(10, 13, 9.8, 12.8), _bar(13.2, 13.5, 11, 11.3)]
    assert "烏雲罩頂" in patterns.detect_candlestick(rows)


def test_harami():
    rows = [_bar(10, 15, 9.5, 14.5), _bar(11.5, 12.5, 11, 12)]
    assert "母子線" in patterns.detect_candlestick(rows)


def test_hammer():
    rows = [_bar(10, 10.7, 8, 10.6)]
    assert "槌子線" in patterns.detect_candlestick(rows)


def test_doji():
    rows = [_bar(10, 11, 9, 10.02)]
    assert "十字星" in patterns.detect_candlestick(rows)


def test_candlestick_skips_missing_ohlc():
    rows = [_bar(10, 12, 9.5, 11), {"open": None, "high": None, "low": None, "close": 13}]
    assert patterns.detect_candlestick(rows) == []


def test_golden_cross():
    # 前 20 天緩跌，最後 4 天急拉，使短均線在最新一天由下往上穿越長均線
    closes = [20 - i * 0.2 for i in range(20)] + [17, 18, 19.5, 21]
    assert "黃金交叉" in patterns.detect_ma_patterns(closes)


def test_death_cross():
    closes = [10 + i * 0.2 for i in range(20)] + [13.8, 13, 11.5, 10, 8.5]
    assert "死亡交叉" in patterns.detect_ma_patterns(closes)


def test_bullish_alignment():
    closes = [10 + i * 0.5 for i in range(25)]  # 持續上漲
    result = patterns.detect_ma_patterns(closes)
    assert "多頭排列" in result


def test_ma_patterns_insufficient_data():
    assert patterns.detect_ma_patterns([10, 11, 12]) == []


def test_three_suns_opening_prosperity():
    # 25 天持續上漲，最新收盤高於 5/10/20 日均線
    closes = [10 + i * 0.5 for i in range(25)]
    assert "三陽開泰" in patterns.detect_ma_patterns(closes)


def test_three_suns_opening_prosperity_not_detected_when_below_ma():
    closes = [20 - i * 0.2 for i in range(20)] + [17, 16.5, 16, 15.8]  # 持續破底，收盤低於均線
    assert "三陽開泰" not in patterns.detect_ma_patterns(closes)


def test_four_seas_roaming_dragon():
    # 60 天持續上漲，最新收盤高於 5/10/20/60 日均線
    closes = [10 + i * 0.3 for i in range(61)]
    assert "四海遊龍" in patterns.detect_ma_patterns(closes)


def test_four_seas_roaming_dragon_not_detected_with_insufficient_data():
    closes = [10 + i * 0.3 for i in range(25)]  # 不足 60 天，算不出季線
    assert "四海遊龍" not in patterns.detect_ma_patterns(closes)
