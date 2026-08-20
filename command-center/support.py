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


def find_swing_lows(bars, window=5):
    """找局部低點：前後各window天內，該天low為最低"""
    lows = []
    for i in range(window, len(bars) - window):
        lo = bars[i]['low']
        neighborhood = bars[i - window:i + window + 1]
        if lo == min(b['low'] for b in neighborhood):
            lows.append(i)
    return lows


def swing_low_support(bars):
    """訊號1：波段低點水平支撐 — 相近低點(±2%)分群，出現≥2次才算有效支撐"""
    idxs = find_swing_lows(bars)
    if not idxs:
        return []
    groups = []
    for i in idxs:
        price = bars[i]['low']
        placed = False
        for g in groups:
            gprice = sum(g['prices']) / len(g['prices'])
            if abs(price - gprice) / gprice <= 0.02:
                g['prices'].append(price)
                g['idxs'].append(i)
                placed = True
                break
        if not placed:
            groups.append({'prices': [price], 'idxs': [i]})
    levels = []
    for g in groups:
        if len(g['idxs']) < 2:
            continue
        avg_price = sum(g['prices']) / len(g['prices'])
        strength = 1 + len(g['idxs'])
        sources = [f"波段低點×{len(g['idxs'])}次"]
        for i in g['idxs']:
            if bottoming_pattern(bars, i):
                strength += 1
                sources.append(f"{bars[i]['date']}止跌K線")
            if volume_confirmed(bars, i):
                strength += 1
                sources.append(f"{bars[i]['date']}量能放大")
        levels.append({'price': round(avg_price, 2), 'strength': min(strength, 5), 'sources': sources})
    return levels


def round_number_support(bars, current_price):
    """訊號4：整數關卡 — 依現價量級自動選單位，找現價下方最近的1-2個整數價位"""
    if current_price >= 1000:
        unit = 100
    elif current_price >= 100:
        unit = 50
    elif current_price >= 20:
        unit = 10
    else:
        unit = 5
    lower = (int(current_price) // unit) * unit
    candidates = [lower] if lower < current_price else []
    candidates.append(lower - unit)
    levels = []
    for level_price in candidates:
        if level_price <= 0:
            continue
        strength = 1
        sources = [f"整數關卡{level_price}"]
        for b in bars:
            if abs(b['low'] - level_price) / level_price <= 0.015 and has_long_lower_shadow(b):
                strength += 1
                sources.append(f"{b['date']}長下影線")
        levels.append({'price': float(level_price), 'strength': min(strength, 5), 'sources': sources})
    return levels
