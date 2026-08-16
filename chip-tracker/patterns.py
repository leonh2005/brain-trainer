# -*- coding: utf-8 -*-
"""K 線與均線型態偵測：純函式，輸入依日期由舊到新排序。"""


def _body(row: dict) -> float:
    return row["close"] - row["open"]


def detect_candlestick(rows: list) -> list:
    """rows：依日期舊到新，每筆需含 open/high/low/close。回傳最新一天成立的型態名稱清單。"""
    rows = [r for r in rows if None not in (r.get("open"), r.get("high"), r.get("low"), r.get("close"))]
    if not rows:
        return []

    found = []
    last = rows[-1]
    rng = last["high"] - last["low"]
    body = abs(_body(last))

    if rng > 0 and body / rng < 0.1:
        found.append("十字星")

    upper = last["high"] - max(last["open"], last["close"])
    lower = min(last["open"], last["close"]) - last["low"]
    if body > 0 and lower >= body * 2 and upper <= body * 0.5:
        found.append("槌子線")

    if len(rows) >= 2:
        prev, cur = rows[-2], rows[-1]
        if _body(prev) < 0 and _body(cur) > 0 and cur["open"] <= prev["close"] and cur["close"] >= prev["open"]:
            found.append("多頭吞噬")
        if _body(prev) > 0 and _body(cur) < 0 and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
            found.append("空頭吞噬")
        if (abs(_body(cur)) < abs(_body(prev))
                and max(cur["open"], cur["close"]) <= max(prev["open"], prev["close"])
                and min(cur["open"], cur["close"]) >= min(prev["open"], prev["close"])):
            found.append("母子線")
        if _body(prev) > 0 and _body(cur) < 0:
            mid = (prev["open"] + prev["close"]) / 2
            if cur["open"] > prev["close"] and cur["close"] < mid:
                found.append("烏雲罩頂")

    return found


def _sma(values: list, n: int):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def detect_ma_patterns(closes: list, short: int = 5, mid: int = 10, long: int = 20, quarter: int = 60) -> list:
    """closes：收盤價，依日期舊到新排序。回傳目前均線型態清單。"""
    if len(closes) < long + 1:
        return []

    def mas_at(upto):
        c = closes[:upto]
        return _sma(c, short), _sma(c, mid), _sma(c, long)

    s_now, m_now, l_now = mas_at(len(closes))
    s_prev, _m_prev, l_prev = mas_at(len(closes) - 1)
    if None in (s_now, m_now, l_now, s_prev, l_prev):
        return []

    found = []
    if s_prev <= l_prev and s_now > l_now:
        found.append("黃金交叉")
    if s_prev >= l_prev and s_now < l_now:
        found.append("死亡交叉")
    if s_now > m_now > l_now:
        found.append("多頭排列")
    if s_now < m_now < l_now:
        found.append("空頭排列")

    if closes[-1] > max(s_now, m_now, l_now):
        found.append("三陽開泰")

    q_now = _sma(closes, quarter)
    if q_now is not None and closes[-1] > max(s_now, m_now, l_now, q_now):
        found.append("四海遊龍")

    return found
