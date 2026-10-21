# -*- coding: utf-8 -*-
"""波動分數：第 F 日收盤後，評估隔日（F+1）出現大波動的程度（0～5 分）。

每個條件都是 2023–2025 找到、2026 驗證過的關係（全市場 1,001 檔，扣除 F 日已漲停）。
分數代表「大起大落」的可能，不代表漲跌方向：分數高時，隔日漲停與大跌機率同時上升，
平均報酬反而略為轉負。
"""

LIMIT_UP = 0.095
MIN_BARS = 6


def score(bars):
    """bars：依日期由舊到新、至少含 open/high/low/close/volume。回傳 dict 或 None。"""
    bars = [b for b in bars if all(b.get(k) is not None for k in ("open", "high", "low", "close", "volume"))]
    if len(bars) < MIN_BARS:
        return None
    last, prev = bars[-1], bars[-2]
    ret = last["close"] / prev["close"] - 1
    if ret >= LIMIT_UP:
        return {"score": None, "max": 5, "date": last.get("date"), "reason": "今日已漲停，此分數不適用", "items": []}

    ret3 = last["close"] / bars[-4]["close"] - 1
    rng = (last["high"] - last["low"]) / last["close"]
    gap = last["open"] / prev["close"] - 1
    vol_ma5 = sum(b["volume"] for b in bars[-6:-1]) / 5
    vol_ratio = last["volume"] / vol_ma5 if vol_ma5 else 0

    items = [
        {"label": "當日漲幅 ≥ 5%", "hit": ret >= 0.05, "value": f"{ret*100:+.1f}%"},
        {"label": "近三日累計 ≥ 10%", "hit": ret3 >= 0.10, "value": f"{ret3*100:+.1f}%"},
        {"label": "當日振幅 ≥ 7%", "hit": rng >= 0.07, "value": f"{rng*100:.1f}%"},
        {"label": "跳空 ≥ 2%", "hit": gap >= 0.02, "value": f"{gap*100:+.1f}%"},
        {"label": "量比 ≥ 2 倍（對 5 日均量）", "hit": vol_ratio >= 2, "value": f"{vol_ratio:.1f}倍"},
    ]
    return {"score": sum(1 for i in items if i["hit"]), "max": 5, "date": last.get("date"), "reason": None, "items": items}
