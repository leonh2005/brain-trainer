"""
價位建議與倉位計算：
  - Shioaji 即時報價（fallback TWSE）
  - Kelly-Fibonacci API 計算建議金額
  - 根據方向與信心算出進場、停損、目標價
"""
import os
from dataclasses import dataclass

import aiohttp
import requests
import warnings

warnings.filterwarnings("ignore")

KELLY_URL = os.environ.get("KELLY_URL", "http://localhost:5700")


@dataclass
class PriceInfo:
    current: float        # 即時/收盤價
    entry: float          # 建議進場限價
    stop_loss: float      # 停損
    target: float         # 目標價
    shares: int           # 建議零股股數
    amount: int           # 建議金額（元）


def _confidence_to_winrate(confidence: int) -> float:
    """信心分數 1-10 → 勝率估計"""
    mapping = {6: 52, 7: 57, 8: 62, 9: 68, 10: 75}
    return mapping.get(confidence, 50)


def _price_levels(price: float, direction: str, confidence: int) -> tuple[float, float, float]:
    """根據方向與信心算出進場、停損、目標（簡單百分比）"""
    stop_pct  = {6: 0.025, 7: 0.02, 8: 0.015, 9: 0.015, 10: 0.01}.get(confidence, 0.02)
    target_pct = stop_pct * 2.0   # 2:1 reward/risk

    if direction == "多":
        entry     = round(price * 1.001, 1)   # 略高於現價，確保成交
        stop_loss = round(price * (1 - stop_pct), 1)
        target    = round(price * (1 + target_pct), 1)
    else:  # 空（目前僅供參考，零股不做放空）
        entry     = round(price * 0.999, 1)
        stop_loss = round(price * (1 + stop_pct), 1)
        target    = round(price * (1 - target_pct), 1)

    return entry, stop_loss, target


async def get_quote(symbol: str) -> float | None:
    """從 TWSE API 取得最新收盤價（免登入）"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "Mozilla/5.0"},
                ssl=False,
            ) as resp:
                data = await resp.json(content_type=None)
                for s in data:
                    if s.get("Code", "").strip() == symbol:
                        price = float(str(s.get("ClosingPrice", "0")).replace(",", ""))
                        return price if price > 0 else None
    except Exception as e:
        print(f"[pricer] TWSE 報價失敗 {symbol}: {e}")
    return None


async def calc_position(
    symbol: str,
    direction: str,
    confidence: int,
    capital: float,
) -> PriceInfo | None:
    price = await get_quote(symbol)
    if not price:
        return None

    entry, stop_loss, target = _price_levels(price, direction, confidence)
    win_rate = _confidence_to_winrate(confidence)

    # 呼叫 kelly-fibonacci
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KELLY_URL}/calculate",
                json={
                    "capital": capital,
                    "win_rate": win_rate,
                    "fib_level": 61.8,
                    "kitchin": 0, "juglar": 0,
                    "kuznets": 0, "kondratiev": 0,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
    except Exception as e:
        print(f"[pricer] kelly-fibonacci 失敗: {e}")
        # fallback：2% 固定倉位
        result = {"half_kelly_amt": capital * 0.02}

    amount = int(result.get("half_kelly_amt", capital * 0.02))
    shares = max(1, int(amount / entry))

    return PriceInfo(
        current=price,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        shares=shares,
        amount=int(shares * entry),
    )
