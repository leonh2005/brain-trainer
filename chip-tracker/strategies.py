# -*- coding: utf-8 -*-
"""InStock 10 個選股策略，移植自 myhhub/stock instock/core/strategy/*.py。

數值邏輯照抄，僅做兩項必要調整：
- p_change 由收盤價自行計算後放在 DataFrame（原版由上游補好）
- high_tight_flag 改以「近 5 日三大法人合計買超 > 0」取代龍虎榜 istop
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import talib as tl

# ── 共用 ──────────────────────────────────────────────────────


def prepare_frame(rows: list) -> pd.DataFrame:
    """DB 列 → 策略用 DataFrame（date 升冪，含 p_change / 三大法人）。

    FinMind 偶有收盤 0 或成交量 0 的壞列，視同沒有這個交易日直接剔除。
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[(df["close"].fillna(0) > 0) & (df["volume"].fillna(0) > 0)]
    df = df.sort_values("date").reset_index(drop=True)
    prev = df["close"].shift(1)
    df["p_change"] = (df["close"] / prev - 1) * 100
    df["p_change"] = df["p_change"].fillna(0.0)
    return df


def _cut(data: pd.DataFrame, date) -> pd.DataFrame:
    end_date = date if isinstance(date, str) else date.strftime("%Y-%m-%d")
    return data.loc[data["date"] <= end_date].copy()


def _ma(data: pd.DataFrame, period: int, col: str) -> None:
    data[col] = tl.MA(data["close"].values, timeperiod=period)
    data[col] = np.nan_to_num(data[col].values, nan=0.0)


def _recent_inst_buy(data: pd.DataFrame, days: int = 5) -> bool:
    tail = data.tail(days)
    net = (tail["foreign_net"].fillna(0) + tail["trust_net"].fillna(0)
           + tail["dealer_net"].fillna(0)).sum()
    return bool(net > 0)


# ── 策略 ──────────────────────────────────────────────────────


def check_volume(code_name, data, date=None, threshold=60):
    """放量上涨：漲幅≥2%、收紅、成交額≥2億、量≥5日均量2倍。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data) < threshold:
        return False
    if data.iloc[-1]["p_change"] < 2 or data.iloc[-1]["close"] < data.iloc[-1]["open"]:
        return False

    data.loc[:, "vol_ma5"] = tl.MA(data["volume"].values.astype(float), timeperiod=5)
    data["vol_ma5"] = np.nan_to_num(data["vol_ma5"].values, nan=0.0)

    data = data.tail(n=threshold + 1)
    if len(data) < threshold + 1:
        return False

    last_close = data.iloc[-1]["close"]
    last_vol = data.iloc[-1]["volume"]
    if last_close * last_vol < 200000000:
        return False

    data = data.head(n=threshold)
    mean_vol = data.iloc[-1]["vol_ma5"]
    return last_vol / mean_vol >= 2 if mean_vol else False


def keep_increasing(code_name, data, date=None, threshold=30):
    """均线多头：MA30 逐段走高且 30 日漲幅 > 20%。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data) < threshold:
        return False
    _ma(data, 30, "ma30")
    data = data.tail(n=threshold)
    step1 = round(threshold / 3)
    step2 = round(threshold * 2 / 3)
    return bool(data.iloc[0]["ma30"] < data.iloc[step1]["ma30"] < data.iloc[step2]["ma30"]
                < data.iloc[-1]["ma30"] and data.iloc[-1]["ma30"] > 1.2 * data.iloc[0]["ma30"])


def backtrace_ma250(code_name, data, date=None, threshold=60):
    """回踩年线：60 日內創高、前半段由年線下突破、後段站穩年線且縮量回踩。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < 250:
        return False
    _ma(data, 250, "ma250")
    data = data.tail(n=threshold)

    lowest_row = [1000000, 0, ""]
    highest_row = [0, 0, ""]
    recent_lowest_row = [1000000, 0, ""]

    for _close, _volume, _date in zip(data["close"].values, data["volume"].values, data["date"].values):
        if _close > highest_row[0]:
            highest_row = [_close, _volume, _date]
        elif _close < lowest_row[0]:
            lowest_row = [_close, _volume, _date]

    if lowest_row[1] == 0 or highest_row[1] == 0:
        return False

    data_front = data.loc[(data["date"] < highest_row[2])]
    data_end = data.loc[(data["date"] >= highest_row[2])]
    if data_front.empty:
        return False
    if not (data_front.iloc[0]["close"] < data_front.iloc[0]["ma250"]
            and data_front.iloc[-1]["close"] > data_front.iloc[-1]["ma250"]):
        return False

    if not data_end.empty:
        for _close, _ma250 in zip(data_end["close"].values, data_end["ma250"].values):
            if _close < _ma250:
                return False
        idx = data_end["close"].idxmin()
        recent_lowest_row = [data_end.loc[idx, "close"], data_end.loc[idx, "volume"],
                             data_end.loc[idx, "date"]]
    if not recent_lowest_row[2]:
        return False

    date_diff = (datetime.strptime(recent_lowest_row[2], "%Y-%m-%d")
                 - datetime.strptime(highest_row[2], "%Y-%m-%d"))
    if not (timedelta(days=10) <= date_diff <= timedelta(days=50)):
        return False
    return highest_row[1] / recent_lowest_row[1] > 2 and recent_lowest_row[0] / highest_row[0] < 0.8


def breakthrough_platform(code_name, data, date=None, threshold=60):
    """突破平台：60 日內放量突破 60 日線，且突破前貼著均線盤整。"""
    origin_data = data
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    _ma(data, 60, "ma60")
    data = data.tail(n=threshold)

    breakthrough_row = None
    for _close, _open, _date, _ma60 in zip(data["close"].values, data["open"].values,
                                          data["date"].values, data["ma60"].values):
        if _open < _ma60 <= _close:
            if check_volume(code_name, origin_data,
                            date=datetime.strptime(_date, "%Y-%m-%d").date(), threshold=threshold):
                breakthrough_row = _date
                break
    if breakthrough_row is None:
        return False

    data_front = data.loc[(data["date"] < breakthrough_row) & (data["ma60"] > 0)]
    for _close, _ma60 in zip(data_front["close"].values, data_front["ma60"].values):
        if not (-0.05 < ((_ma60 - _close) / _ma60) < 0.2):
            return False
    return True


def low_backtrace_increase(code_name, data, date=None, threshold=60):
    """无大幅回撤：60 日漲幅 > 60%，且期間無單日/兩日重挫。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    data = data.tail(n=threshold)

    ratio_increase = (data.iloc[-1]["close"] - data.iloc[0]["close"]) / data.iloc[0]["close"]
    if ratio_increase < 0.6:
        return False

    # 原版把 previous_open 初始化成 -1000000，第一根 K 棒必定觸發「兩日高開低走」而永遠回 False。
    # 這裡改成第一根不檢查兩日條件（原意是跟「前一日」比），否則策略是死的。
    previous_p_change = None
    previous_open = None
    for _p_change, _close, _open in zip(data["p_change"].values, data["close"].values, data["open"].values):
        if _p_change < -7 or (_close - _open) / _open * 100 < -7:
            return False
        if previous_p_change is not None and (
                previous_p_change + _p_change < -10
                or (_close - previous_open) / previous_open * 100 < -10):
            return False
        previous_p_change = _p_change
        previous_open = _open
    return True


def turtle_trade(code_name, data, date=None, threshold=60):
    """海龟交易法则：收盘价創 60 日新高。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    data = data.tail(n=threshold)
    return bool(data.iloc[-1]["close"] >= data["close"].max())


def high_tight_flag(code_name, data, date=None, threshold=60):
    """高而窄的旗形：24~10 日前區間漲近 2 倍且連兩日漲停。

    原版需龍虎榜有機構（istop），這裡改為近 5 日三大法人合計買超 > 0。
    """
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    if not _recent_inst_buy(data, days=5):
        return False

    data = data.tail(n=24).head(n=14)
    low = data["low"].values.min()
    if data.iloc[-1]["high"] / low < 1.9:
        return False

    previous_p_change = 0.0
    for _p_change in data["p_change"].values:
        if _p_change >= 9.5:
            if previous_p_change >= 9.5:
                return True
            previous_p_change = _p_change
        else:
            previous_p_change = 0.0
    return False


def climax_limitdown(code_name, data, date=None, threshold=60):
    """放量跌停：跌 > 9.5%、成交額 ≥ 2 億、量 ≥ 5 日均量 4 倍。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    if data.iloc[-1]["p_change"] > -9.5:
        return False

    data.loc[:, "vol_ma5"] = tl.MA(data["volume"].values.astype(float), timeperiod=5)
    data["vol_ma5"] = np.nan_to_num(data["vol_ma5"].values, nan=0.0)
    data = data.tail(n=threshold + 1)
    if len(data.index) < threshold + 1:
        return False

    last_close = data.iloc[-1]["close"]
    last_vol = data.iloc[-1]["volume"]
    if last_close * last_vol < 200000000:
        return False

    data = data.head(n=threshold)
    mean_vol = data.iloc[-1]["vol_ma5"]
    return bool(last_vol / mean_vol >= 4) if mean_vol else False


def low_atr(code_name, data, date=None, ma_short=30, ma_long=250, threshold=10):
    """低ATR成长：上市滿 250 日、10 日平均振幅 < 10%、高低差 > 10%。"""
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < ma_long:
        return False
    data = data.tail(n=threshold)
    if len(data.index) < threshold:
        return False

    total_change = 0.0
    for _p_change in data["p_change"].values:
        if _p_change > 0:
            total_change += abs(_p_change)
        elif _p_change < 0:
            total_change += abs(_p_change)
    atr = total_change / len(data.index)
    if atr > 10:
        return False

    # 照抄原版的 elif：連續創新高時 lowest 不會被更新（實測唯一差異點，維持一致）
    highest, lowest = 0.0, 1000000.0
    for _close in data["close"].values:
        if _close > highest:
            highest = _close
        elif _close < lowest:
            lowest = _close
    return bool((highest - lowest) / lowest > 1.1)


def parking_apron(code_name, data, date=None, threshold=15):
    """停机坪：15 日內放量漲停，之後 3 日高檔強勢盤整。"""
    origin_data = data
    data = _cut(data, date) if date is not None else data.copy()
    if len(data.index) < threshold:
        return False
    data = data.tail(n=threshold)

    for _close, _p_change, _date in zip(data["close"].values, data["p_change"].values, data["date"].values):
        if _p_change > 9.5:
            if turtle_trade(code_name, origin_data,
                            date=datetime.strptime(_date, "%Y-%m-%d").date(), threshold=threshold):
                if _check_parking_internal(data, _date, _close):
                    return True
    return False


def _check_parking_internal(data: pd.DataFrame, limitup_date: str, limitup_price: float) -> bool:
    after = data.loc[(data["date"] > limitup_date)].head(n=3)
    if len(after.index) < 3:
        return False
    day1 = after.iloc[0]
    day23 = after.tail(n=2)
    if not (day1["close"] > limitup_price and day1["open"] > limitup_price
            and 0.97 < day1["close"] / day1["open"] < 1.03):
        return False
    for _close, _p_change, _open in zip(day23["close"].values, day23["p_change"].values, day23["open"].values):
        if not (0.97 < (_close / _open) < 1.03 and -5 < _p_change < 5
                and _close > limitup_price and _open > limitup_price):
            return False
    return True


# ── 註冊表 ────────────────────────────────────────────────────

STRATEGIES = [
    ("放量上涨", check_volume),
    ("均线多头", keep_increasing),
    ("停机坪", parking_apron),
    ("回踩年线", backtrace_ma250),
    ("突破平台", breakthrough_platform),
    ("无大幅回撤", low_backtrace_increase),
    ("海龟交易法则", turtle_trade),
    ("高而窄的旗形", high_tight_flag),
    ("放量跌停", climax_limitdown),
    ("低ATR成长", low_atr),
]
