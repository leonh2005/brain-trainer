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
        volume_bonus = False
        for i in g['idxs']:
            if bottoming_pattern(bars, i):
                strength += 1
                sources.append(f"{bars[i]['date']}止跌K線")
            if volume_confirmed(bars, i):
                sources.append(f"{bars[i]['date']}量能放大")
                volume_bonus = True
        if volume_bonus:
            strength += 1
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


def trendline_support(bars):
    """訊號2：上升趨勢線支撐 — 取最近2~3個波段低點做線性回歸，斜率須為正才算上升趨勢；
    若近3天收盤明顯跌破線且量能配合，視為失效不列入"""
    idxs = find_swing_lows(bars)
    if len(idxs) < 2:
        return []
    recent = idxs[-3:] if len(idxs) >= 3 else idxs[-2:]
    xs, ys = recent, [bars[i]['low'] for i in recent]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return []
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    if slope <= 0:
        return []
    intercept = mean_y - slope * mean_x
    last_idx = len(bars) - 1
    line_price = slope * last_idx + intercept
    current_close = bars[-1]['close']
    for i in range(max(0, last_idx - 2), last_idx + 1):
        line_at_i = slope * i + intercept
        if bars[i]['close'] < line_at_i * 0.995 and volume_confirmed(bars, i):
            return []
    if line_price >= current_close:
        return []
    return [{
        'price': round(line_price, 2), 'strength': 3,
        'sources': [f"上升趨勢線(連接{bars[recent[0]]['date']}~{bars[recent[-1]]['date']}低點)"],
    }]


def _ma_series(bars, n):
    if len(bars) < n:
        return None
    return [sum(b['close'] for b in bars[i - n + 1:i + 1]) / n for i in range(n - 1, len(bars))]


def ma_support(bars):
    """訊號3：均線動態支撐 MA20/60/120/250 — 均線本身須向上，現價須在均線上方才算有效支撐"""
    current_close = bars[-1]['close']
    levels = []
    for n in (20, 60, 120, 250):
        if len(bars) < n + 5:
            continue
        ma_series = _ma_series(bars, n)
        ma_now, ma_5ago = ma_series[-1], ma_series[-6] if len(ma_series) >= 6 else ma_series[0]
        if ma_now <= ma_5ago or current_close < ma_now:
            continue
        strength = 2
        sources = [f"MA{n}向上"]
        offset = len(bars) - len(ma_series)
        for i in range(max(0, len(bars) - 20), len(bars)):
            j = i - offset
            if 0 <= j < len(ma_series):
                ma_i = ma_series[j]
                if abs(bars[i]['low'] - ma_i) / ma_i <= 0.015 and bottoming_pattern(bars, i):
                    strength += 1
                    sources.append(f"{bars[i]['date']}止跌於MA{n}")
        levels.append({'price': round(ma_now, 2), 'strength': min(strength, 5), 'sources': sources})
    return levels


def fib_support(bars, lookback=120):
    """訊號5：斐波那契回撤 — 取近期波段高低點，回撤 38.2% / 50% / 61.8% 為支撐。

    上漲段（低點在前、高點在後）才適用回撤支撐：以「低→高」的量測，
    回撤位 = 高點 - (高點-低點) × 比例。只保留落在現價之下者。
    """
    if len(bars) < 40:
        return []
    seg = bars[-lookback:] if len(bars) > lookback else bars
    hi_i = max(range(len(seg)), key=lambda i: seg[i]['high'])
    lo_i = min(range(len(seg)), key=lambda i: seg[i]['low'])
    if lo_i >= hi_i:          # 高點在低點之前＝下跌段，回撤不作為支撐
        return []
    # 近期性要求：高點必須落在區間後半段。
    # 只檢查「低點在前、高點在後」不夠——「早低、中高、長期下殺」也滿足，
    # 那會拿 100 天前的舊波段來算回撤位，對現在的支撐沒意義。
    if hi_i < len(seg) * 0.5:
        return []
    hi, lo = seg[hi_i]['high'], seg[lo_i]['low']
    rng = hi - lo
    if rng <= 0:
        return []
    current = bars[-1]['close']
    levels = []
    for ratio, label in ((0.382, '38.2%'), (0.5, '50%'), (0.618, '61.8%')):
        price = hi - rng * ratio
        if price >= current or price <= 0:
            continue
        strength = 3 if ratio == 0.618 else 2   # 黃金比例權重較高
        sources = [f"斐波那契{label}（{seg[lo_i]['date']}低→{seg[hi_i]['date']}高）"]
        base = len(bars) - len(bars[-30:])      # bars[-30:] 在母陣列中的起點
        for offset, b in enumerate(bars[-30:]):
            if abs(b['low'] - price) / price <= 0.015 and bottoming_pattern(bars, base + offset):
                strength += 1
                sources.append(f"{b['date']}止跌於Fib{label}")
        levels.append({'price': round(price, 2), 'strength': min(strength, 5), 'sources': sources})
    return levels


def pivot_support(bars):
    """訊號6：樞紐點（Standard Pivot）— 以前一根K棒的 H/L/C 計算 S1/S2/S3。

    這是日內與短線最常用的當日支撐，只保留現價之下者。
    """
    if len(bars) < 2:
        return []
    prev = bars[-2]
    h, l, c = prev['high'], prev['low'], prev['close']
    pp = (h + l + c) / 3
    current = bars[-1]['close']
    cands = [
        ('S1', 2 * pp - h, 2),
        ('S2', pp - (h - l), 3),
        ('S3', l - 2 * (h - pp), 2),
    ]
    levels = []
    for label, price, strength in cands:
        if price >= current or price <= 0:
            continue
        levels.append({
            'price': round(price, 2), 'strength': strength,
            'sources': [f"樞紐點{label}（依{prev['date']} H/L/C）"],
        })
    return levels


def volume_profile_support(bars, bins=24, lookback=120):
    """訊號7：成交量密集區（Volume Profile / HVN）— 把價格分箱累加成交量，
    取現價下方成交量最大的價位。高成交量節點通常是籌碼密集、容易形成支撐的位置。
    """
    if len(bars) < 30:
        return []
    seg = bars[-lookback:] if len(bars) > lookback else bars
    highs = [b['high'] for b in seg]
    lows = [b['low'] for b in seg]
    top, bot = max(highs), min(lows)
    if top <= bot:
        return []
    width = (top - bot) / bins
    bucket = [0.0] * bins
    for b in seg:
        # 用典型價 (H+L+C)/3 落點，比單看收盤價更接近當日實際成交重心
        if width <= 0:
            continue
        typical = (b['high'] + b['low'] + b['close']) / 3
        idx = min(bins - 1, max(0, int((typical - bot) / width)))
        bucket[idx] += b['volume']
    current = bars[-1]['close']
    total = sum(bucket) or 1
    levels = []
    for i, v in enumerate(bucket):
        if v <= 0:
            continue
        price = bot + (i + 0.5) * width
        if price >= current or price <= 0:
            continue
        share = v / total
        if share < 0.08:            # 佔比太低的箱子不算密集區
            continue
        levels.append({
            'price': round(price, 2), 'strength': 0,   # 強度由 share 決定，見下
            'sources': [f"成交量密集區（{share*100:.0f}% 成交量）"], '_share': share,
        })
    # 依實際佔比取最密集的兩個（不是依 strength，否則較遠的箱會擠掉較近的）
    levels.sort(key=lambda x: x['_share'], reverse=True)
    top = levels[:2]
    for lv in top:
        lv['strength'] = 3 if lv.pop('_share') >= 0.15 else 2
    return top


def vwap_support(bars, lookback=60):
    """訊號8：VWAP（成交量加權平均價）— 以近 N 日累積成交量加權計算，
    代表這段期間的平均持有成本，回檔至此常有支撐。
    """
    seg = [b for b in bars[-lookback:] if b.get('volume', 0) > 0]
    if len(seg) < 10:
        return []
    pv = sum(((b['high'] + b['low'] + b['close']) / 3) * b['volume'] for b in seg)
    vv = sum(b['volume'] for b in seg)
    if vv <= 0:
        return []
    vwap = pv / vv
    current = bars[-1]['close']
    if vwap >= current or vwap <= 0:
        return []
    return [{'price': round(vwap, 2), 'strength': 2,
             'sources': [f"VWAP（近{len(seg)}日平均成本）"]}]


# 訊號種類前綴 → 種類代號。用來判斷「這個支撐被幾種不同訊號支持」，
# 避免同一訊號的多個相近價位被誤認為多重確認。
_KIND_PREFIXES = (
    ("波段低點", "swing"), ("上升趨勢線", "trend"), ("MA", "ma"),
    ("整數關卡", "round"), ("斐波那契", "fib"), ("樞紐點", "pivot"),
    ("成交量密集區", "vp"), ("VWAP", "vwap"),
)


def _source_kind(source: str) -> str:
    for prefix, kind in _KIND_PREFIXES:
        if source.startswith(prefix):
            return kind
    return source[:6]


def analyze_support(bars):
    """整合所有訊號，合併相近價位(±1.5%)，回傳依price由高到低排序的支撐位列表"""
    if len(bars) < 30:
        return []
    all_levels = (swing_low_support(bars) + trendline_support(bars)
                  + ma_support(bars) + round_number_support(bars, bars[-1]['close'])
                  + fib_support(bars) + pivot_support(bars)
                  + volume_profile_support(bars) + vwap_support(bars))
    merged = []
    for lv in sorted(all_levels, key=lambda x: x['price'], reverse=True):
        placed = False
        for m in merged:
            if abs(lv['price'] - m['price']) / m['price'] <= 0.015:
                m['strength'] = min(5, m['strength'] + 1)
                m['sources'] += lv['sources']
                m['price'] = round((m['price'] + lv['price']) / 2, 2)
                placed = True
                break
        if not placed:
            merged.append(dict(lv))
    merged.sort(key=lambda x: x['price'], reverse=True)
    # 重算強度：只看「有幾種不同的訊號支持這個價位」。
    # 合併時逐筆 +1 會讓同源重複自我加乘（例如平盤時 pivot S1/S2/S3 同價），
    # 8 個訊號下任何價位都能湊滿 5，強度就沒有鑑別度了。
    for m in merged:
        kinds = {_source_kind(s) for s in m['sources']}
        m['strength'] = min(5, len(kinds))
        m['sources'] = list(dict.fromkeys(m['sources']))   # 去重但保留順序

    current_price = bars[-1]['close']
    below_current = [m for m in merged if m['price'] <= current_price]
    nearby = [m for m in below_current if m['price'] >= current_price * 0.85]
    if nearby:
        return nearby[:5]
    if below_current:
        # 急漲股常見：所有支撐都被漲勢甩開超過15%，仍附上最近一個供參考，並標記距離
        nearest = dict(below_current[0])
        nearest['far'] = True
        nearest['distance_pct'] = round((current_price - nearest['price']) / current_price * 100, 1)
        return [nearest]
    return []
