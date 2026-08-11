# -*- coding: utf-8 -*-
"""盤整突破策略：用 ADX + 布林通道寬度客觀偵測盤整區間，
再套用課程教的頸線規則（區間最高/最低價）判斷有效突破。

參考來源：
- ADX < 15 連續數日 = 盤整；ADX 站回 25 以上 = 有方向性行情（業界常見門檻）
- 布林通道寬度 = (上軌-下軌)/中軌，數值窄代表盤整
- 頸線突破：收盤價站上/跌破區間高低點，且量能放大才算有效突破
- 反轉 vs 敘事：看盤整前的原始趨勢（月線斜率）決定跌破/突破的意義
"""

from typing import Optional

MIN_BARS = 60
ADX_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
ADX_CONSOLIDATION_MAX = 15.0   # ADX 低於此值視為盤整
CONSOLIDATION_MIN_DAYS = 10    # 至少連續這麼多天 ADX 都低才算確認盤整
VOLUME_MULTIPLIER = 1.5        # 突破當日量能要超過近5日均量的倍數
BODY_PCT_MIN = 1.0             # 突破棒最低實體漲跌幅門檻（%）


def _true_range(highs, lows, closes, i):
    if i == 0:
        return highs[i] - lows[i]
    return max(
        highs[i] - lows[i],
        abs(highs[i] - closes[i - 1]),
        abs(lows[i] - closes[i - 1]),
    )


def _wilder_smooth(values, period):
    """Wilder's smoothing：第一個值是前 period 個的簡單平均，之後遞迴平滑成移動平均。
    對 TR/+DM/-DM 用這個平滑，因為是同一個 period 做分母/分子，比例不受影響；
    對 DX 平滑成 ADX 時必須是真平均（不能是加總），否則 ADX 會超過 100。"""
    if len(values) < period:
        return []
    smoothed = [sum(values[:period]) / period]
    for v in values[period:]:
        smoothed.append((smoothed[-1] * (period - 1) + v) / period)
    return smoothed


def adx_series(highs, lows, closes, period=ADX_PERIOD):
    """回傳跟 highs 等長、前面補 None 的 ADX 序列（標準 Wilder 演算法）。"""
    n = len(closes)
    if n < period * 2:
        return [None] * n

    plus_dm = [0.0]
    minus_dm = [0.0]
    tr = [_true_range(highs, lows, closes, 0)]
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr.append(_true_range(highs, lows, closes, i))

    smoothed_tr = _wilder_smooth(tr, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    dx = []
    for str_, spdm, smdm in zip(smoothed_tr, smoothed_plus_dm, smoothed_minus_dm):
        if str_ == 0:
            dx.append(0.0)
            continue
        plus_di = 100 * spdm / str_
        minus_di = 100 * smdm / str_
        denom = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denom if denom else 0.0)

    adx_smoothed = _wilder_smooth(dx, period)

    result = [None] * n
    # dx[0] 對應第 period 根 K（index period-1），adx_smoothed[0] 對應 dx 裡的第 period 個
    dx_start_idx = period
    adx_start_idx = dx_start_idx + period - 1
    for i, val in enumerate(adx_smoothed):
        idx = adx_start_idx + i
        if idx < n:
            result[idx] = val
    return result


def bollinger_bandwidth_series(closes, period=BB_PERIOD, num_std=BB_STD):
    """回傳跟 closes 等長、前面補 None 的布林通道寬度序列。"""
    n = len(closes)
    result = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        mid = sum(window) / period
        variance = sum((c - mid) ** 2 for c in window) / period
        std = variance ** 0.5
        if mid == 0:
            continue
        upper = mid + num_std * std
        lower = mid - num_std * std
        result[i] = (upper - lower) / mid
    return result


def _find_consolidation_window(adx_vals):
    """從序列尾端往前找連續 ADX < 門檻的區間，回傳 (start_idx, end_idx)（不含今天）或 None。"""
    n = len(adx_vals)
    end = n - 2  # 不含今天（今天可能是突破日）
    if end < 0 or adx_vals[end] is None:
        return None
    start = end
    while start - 1 >= 0 and adx_vals[start - 1] is not None and adx_vals[start - 1] < ADX_CONSOLIDATION_MAX:
        start -= 1
    if adx_vals[end] >= ADX_CONSOLIDATION_MAX:
        return None
    if end - start + 1 < CONSOLIDATION_MIN_DAYS:
        return None
    return start, end


def evaluate_breakout(bars: list) -> Optional[dict]:
    """輸入單檔日K（舊到新），符合「盤整突破/跌破」條件則回傳診斷欄位，否則 None。"""
    n = len(bars)
    if n < MIN_BARS:
        return None

    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    adx_vals = adx_series(highs, lows, closes)
    window = _find_consolidation_window(adx_vals)
    if window is None:
        return None
    start, end = window

    upper_neckline = max(highs[start:end + 1])
    lower_neckline = min(lows[start:end + 1])

    bw_vals = bollinger_bandwidth_series(closes)
    consolidation_end_bw = bw_vals[end]  # 用盤整區間最後一天（突破前）的寬度，不能用今天——
    # 今天已經是突破日，價格噴出去會讓布林通道瞬間變寬，用今天驗證等於倒果為因
    valid_bw = [b for b in bw_vals[max(0, n - 126):] if b is not None]
    if consolidation_end_bw is None or not valid_bw or consolidation_end_bw > min(valid_bw) * 1.5:
        # 突破前的布林寬度沒有收窄到近半年低點附近，交叉驗證不通過
        return None

    today_open, today_close, today_vol = opens[-1], closes[-1], volumes[-1]
    if today_open <= 0:
        return None
    body_pct = (today_close - today_open) / today_open * 100
    recent5_vol = volumes[-6:-1]
    vol_ok = bool(recent5_vol) and today_vol > (sum(recent5_vol) / len(recent5_vol)) * VOLUME_MULTIPLIER

    # 盤整前的原始趨勢：用盤整區間起點前 20 天判斷月線斜率
    pre_start = max(0, start - 20)
    if pre_start >= start:
        return None
    ma20_before = sum(closes[pre_start:start]) / (start - pre_start) if start > pre_start else None
    prior_trend = None
    if ma20_before is not None:
        prior_trend = "up" if closes[start] > ma20_before else "down"

    direction = None
    if today_close > upper_neckline and body_pct >= BODY_PCT_MIN and vol_ok:
        direction = "up"
    elif today_close < lower_neckline and abs(body_pct) >= BODY_PCT_MIN and vol_ok:
        direction = "down"
    if direction is None:
        return None

    if direction == "up":
        classify = "反轉轉多" if prior_trend == "down" else "延續多頭"
    else:
        classify = "反轉轉空" if prior_trend == "up" else "延續空頭（敘事）"

    return {
        "close": today_close,
        "volume": today_vol,
        "body_pct": round(body_pct, 2),
        "direction": direction,
        "classify": classify,
        "upper_neckline": round(upper_neckline, 2),
        "lower_neckline": round(lower_neckline, 2),
        "consolidation_days": end - start + 1,
        "adx_today": round(adx_vals[-2], 2) if adx_vals[-2] is not None else None,
        "bb_width_before_breakout": round(consolidation_end_bw, 4),
    }


def scan(n_universe: int, progress_cb=None) -> list:
    import data_fetcher
    universe = data_fetcher.get_universe(n_universe)
    total = len(universe)
    results = []
    for i, stock in enumerate(universe):
        bars = data_fetcher.get_daily_bars(stock["code"])
        matched = evaluate_breakout(bars)
        if matched:
            results.append({**stock, **matched})
        if progress_cb:
            progress_cb(i + 1, total)
    results.sort(key=lambda x: x["volume"], reverse=True)
    return results
