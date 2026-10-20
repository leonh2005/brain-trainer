# -*- coding: utf-8 -*-
"""CYQ 籌碼分佈，移植自 InStock (myhhub/stock) instock/core/kline/cyq.py。

演算法未改，僅：
- 支援 DataFrame 少於 210 天時自動縮窗（原版會取到負 index）
- 額外輸出「套牢區 / 解套壓力」給前端顯示
- 分佈正規化為百分比（原始 xdata 總和 ≈ 累積換手率，不是 100%）
"""

import pandas as pd

REQUIRED_COLUMNS = ("date", "open", "close", "high", "low", "volume", "amount", "amplitude", "turnover")


class CYQCalculator:
    def __init__(self, kdata: pd.DataFrame, accuracy_factor: int = 150,
                 crange: int = 120, cyq_days: int = 210):
        missing = [c for c in REQUIRED_COLUMNS if c not in kdata.columns]
        if missing:
            raise ValueError(f"CYQ 缺少欄位: {missing}")
        self.klinedata = kdata.reset_index(drop=True)
        self.fator = accuracy_factor
        self.range = crange
        self.tradingdays = cyq_days

    def calc(self, index: int) -> dict:
        factor = self.fator
        end = index - self.range + 1
        start = end - self.tradingdays

        if end <= 0:
            kdata = self.klinedata.tail(self.tradingdays)
        else:
            kdata = self.klinedata.iloc[max(0, start):end]
        if kdata.empty:
            raise ValueError("CYQ 計算區間為空")

        maxprice = float(kdata["high"].max())
        minprice = float(kdata["low"].min())
        accuracy = max(0.01, (maxprice - minprice) / (factor - 1))
        currentprice = float(kdata.iloc[-1]["close"])
        boundary = -1

        yrange = []
        for i in range(factor):
            _price = float(f"{minprice + accuracy * i:.2f}")
            yrange.append(_price)
            if boundary == -1 and _price >= currentprice:
                boundary = i

        xdata = [0.0] * factor
        for open_price, close, high, low, turnover in zip(
                kdata["open"].values, kdata["close"].values, kdata["high"].values,
                kdata["low"].values, kdata["turnover"].values):
            if pd.isna(turnover):
                turnover = 0.0
            avg = (open_price + close + high + low) / 4
            turnover_rate = min(1.0, float(turnover) / 100)

            H = int((high - minprice) / accuracy)
            L = int((low - minprice) / accuracy + 0.99)
            is_limit = high == low
            g_point = [factor - 1 if is_limit else 2 / (high - low),
                       int((avg - minprice) / accuracy)]

            for n in range(len(xdata)):
                xdata[n] *= (1 - turnover_rate)

            if is_limit:
                xdata[g_point[1]] += g_point[0] * turnover_rate / 2
            else:
                for j in range(L, H + 1):
                    curprice = minprice + accuracy * j
                    if curprice <= avg:
                        if abs(avg - low) < 1e-8:
                            xdata[j] += g_point[0] * turnover_rate
                        else:
                            xdata[j] += (curprice - low) / (avg - low) * g_point[0] * turnover_rate
                    else:
                        if abs(high - avg) < 1e-8:
                            xdata[j] += g_point[0] * turnover_rate
                        else:
                            xdata[j] += (high - curprice) / (high - avg) * g_point[0] * turnover_rate

        xdata = [float(f"{x:.12g}") for x in xdata]
        total_chips = sum(xdata)
        if total_chips <= 0:
            raise ValueError("CYQ 籌碼總和為 0")

        def get_cost_by_chip(chip: float) -> float:
            sum_chips = 0.0
            for i in range(factor):
                if sum_chips + xdata[i] > chip:
                    return minprice + i * accuracy
                sum_chips += xdata[i]
            return minprice + (factor - 1) * accuracy

        def compute_percent_chips(percent: float) -> dict:
            ps = [(1 - percent) / 2, (1 + percent) / 2]
            pr = [get_cost_by_chip(total_chips * ps[0]), get_cost_by_chip(total_chips * ps[1])]
            return {
                "price_range": [round(pr[0], 2), round(pr[1], 2)],
                "concentration": 0 if pr[0] + pr[1] == 0 else (pr[1] - pr[0]) / (pr[0] + pr[1]),
            }

        # 分界跟圖表一致（boundary = y 首次 >= 現價），避免浮點/四捨五入造成獲利+套牢 ≠ 100%
        split = boundary if boundary >= 0 else factor
        benefit_part = sum(xdata[:split]) / total_chips

        # 套牢區 = 現價以上的籌碼；解套壓力 = 上方籌碼最集中的價位
        above_bins = [(yrange[i], xdata[i]) for i in range(split, factor)]
        below_bins = [(yrange[i], xdata[i]) for i in range(split)]
        overhead = sum(x for _, x in above_bins)
        pressure_price = max(above_bins, key=lambda p: p[1])[0] if above_bins else None
        support_price = max(below_bins, key=lambda p: p[1])[0] if below_bins else None

        pct = [x / total_chips * 100 for x in xdata]
        return {
            "date": str(kdata.iloc[-1]["date"]),
            "close": currentprice,
            "y": yrange,
            "x": [round(v, 4) for v in pct],
            "total_chips": round(total_chips, 6),
            "avg_cost": round(get_cost_by_chip(total_chips * 0.5), 2),
            "benefit_part": round(benefit_part, 4),
            "overhead_ratio": round(overhead / total_chips, 4),
            "pressure_price": pressure_price,
            "support_price": support_price,
            "percent_chips": {"90": compute_percent_chips(0.9), "70": compute_percent_chips(0.7)},
        }


def compute_from_rows(rows: list, accuracy_factor: int = 150, cyq_days: int = 210) -> dict:
    """rows: daily JOIN daily_ext 的 dict 列表（date 升冪）。

    InStock 圖表傳入的 klinedata 長度等於 crange，使 calc() 走 `end <= 0` 分支、
    取最後 cyq_days 根 K 棒；這裡直接把窗口切好再走同一分支，語意一致。
    """
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("無歷史資料可計算 CYQ")
    df = df.tail(cyq_days).reset_index(drop=True)
    if df["turnover"].fillna(0).sum() <= 0:
        raise ValueError("缺已發行股數，無法計算換手率（興櫃股 FinMind 未提供）")
    calc = CYQCalculator(df, accuracy_factor=accuracy_factor,
                         crange=len(df), cyq_days=cyq_days)
    return calc.calc(len(df) - 1)
