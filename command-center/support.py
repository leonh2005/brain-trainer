"""支撐位偵測 — 波段低點、趨勢線、均線、整數關卡、K線形態、成交量 六種訊號綜合判斷
輸入日K陣列（[{date,open,high,low,close,volume}]，舊到新），輸出支撐位列表 [{price,strength,sources}]"""


def is_hammer(bar):
    """錘子線/倒錘子線：下影線或上影線長度 >= 實體2倍，且實體佔全幅 <= 35%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if body / rng > 0.35:
        return False
    return lower >= body * 2 or upper >= body * 2


def is_doji(bar):
    """十字星：實體佔全幅 <= 8%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    return abs(c - o) / rng <= 0.08


def is_bullish_engulfing(prev, cur):
    """看漲吞沒：前一天收黑，今天收紅且實體完全吞沒前一天實體"""
    prev_bear = prev['close'] < prev['open']
    cur_bull = cur['close'] > cur['open']
    if not (prev_bear and cur_bull):
        return False
    return cur['open'] <= prev['close'] and cur['close'] >= prev['open']


def has_long_lower_shadow(bar):
    """長下影線：下影線長度 >= 實體1.5倍，且下影線佔全幅 >= 40%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    return lower >= body * 1.5 and lower / rng >= 0.4


def bottoming_pattern(bars, idx):
    """idx這根K棒是否為止跌形態（錘子/十字星/吞沒/長下影線任一）"""
    bar = bars[idx]
    if is_hammer(bar) or is_doji(bar) or has_long_lower_shadow(bar):
        return True
    if idx > 0 and is_bullish_engulfing(bars[idx - 1], bar):
        return True
    return False


def volume_confirmed(bars, idx):
    """idx這根K棒的量是否 >= 前5日均量"""
    if idx < 5:
        return False
    vol = bars[idx]['volume']
    avg5 = sum(b['volume'] for b in bars[idx - 5:idx]) / 5
    return avg5 > 0 and vol >= avg5
