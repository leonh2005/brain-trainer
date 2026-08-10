# -*- coding: utf-8 -*-
"""回後買上漲策略：多頭趨勢 → 正常回檔（量縮不破前低）→ 進場K棒（大量長紅、站上5日線、破昨高）。"""

from datetime import date
from typing import Callable, Optional

import data_fetcher

MIN_BARS = 65
TREND_LOOKBACK = 5      # 月線向上比較基準（5 個交易日前）
PEAK_WINDOW = 15        # 找波段高點的回看天數
SUPPORT_WINDOW = 20     # 高點前找支撐低點的回看天數
BODY_PCT_MIN = 2.0      # 實體漲幅門檻
STALE_DAYS_MAX = 7      # 最新K棒與今天的日曆天差距上限，超過視為資料不完整（Shioaji 大量請求下偶爾回傳截斷資料）


def _sma(closes: list[float], window: int, end_idx: int) -> float:
    return sum(closes[end_idx - window + 1:end_idx + 1]) / window


def evaluate(bars: list[dict]) -> Optional[dict]:
    """輸入單檔日K（舊到新），符合「回後買上漲」條件則回傳診斷欄位，否則 None。"""
    n = len(bars)
    if n < MIN_BARS:
        return None
    if (date.today() - date.fromisoformat(bars[-1]["date"])).days > STALE_DAYS_MAX:
        return None

    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    ma5 = _sma(closes, 5, n - 1)
    ma20 = _sma(closes, 20, n - 1)
    ma60 = _sma(closes, 60, n - 1)
    ma20_prev = _sma(closes, 20, n - 1 - TREND_LOOKBACK)

    if not (closes[-1] > ma20 > ma60):
        return None
    if not (ma5 > ma20):
        return None
    if not (ma20 > ma20_prev):
        return None

    lookback_start = max(0, n - 1 - PEAK_WINDOW)
    lookback_end = n - 2  # 不含今天
    if lookback_end < lookback_start:
        return None
    window_closes = closes[lookback_start:lookback_end + 1]
    peak_idx = lookback_start + window_closes.index(max(window_closes))

    if peak_idx > n - 3:  # 高點跟今天之間至少要有 1 天回檔
        return None

    pullback_lows = lows[peak_idx + 1:n - 1]
    pullback_vols = volumes[peak_idx + 1:n - 1]
    if not pullback_lows:
        return None
    pullback_low = min(pullback_lows)

    support_window = lows[max(0, peak_idx - SUPPORT_WINDOW):peak_idx]
    if not support_window:
        return None
    if pullback_low <= min(support_window):  # 跌破前低
        return None

    pre_peak_vols = volumes[max(0, peak_idx - 5):peak_idx]
    if not pre_peak_vols:
        return None
    pullback_avg_vol = sum(pullback_vols) / len(pullback_vols)
    pre_peak_avg_vol = sum(pre_peak_vols) / len(pre_peak_vols)
    if pullback_avg_vol >= pre_peak_avg_vol:  # 回檔沒量縮
        return None

    today_open, today_close, today_vol = opens[-1], closes[-1], volumes[-1]
    if today_open <= 0:
        return None
    body_pct = (today_close - today_open) / today_open * 100
    if body_pct < BODY_PCT_MIN:
        return None
    if today_close <= ma5:
        return None
    if today_close <= highs[-2]:  # 沒突破昨天最高點
        return None

    recent5_vol = volumes[-6:-1]
    if not recent5_vol or today_vol <= sum(recent5_vol) / len(recent5_vol):  # 今日量能沒放大
        return None

    prev_close = closes[-2]
    change_pct = (today_close - prev_close) / prev_close * 100 if prev_close else 0.0

    return {
        "close": today_close,
        "volume": today_vol,
        "change_pct": round(change_pct, 2),
        "body_pct": round(body_pct, 2),
        "ma20_dist_pct": round((today_close - ma20) / ma20 * 100, 2),
        "peak_date": bars[peak_idx]["date"],
    }


def scan(n_universe: int, progress_cb: Optional[Callable[[int, int], None]] = None) -> list[dict]:
    universe = data_fetcher.get_universe(n_universe)
    total = len(universe)
    results = []
    for i, stock in enumerate(universe):
        bars = data_fetcher.get_daily_bars(stock["code"])
        matched = evaluate(bars)
        if matched:
            results.append({**stock, **matched})
        if progress_cb:
            progress_cb(i + 1, total)
    results.sort(key=lambda x: x["volume"], reverse=True)
    return results
