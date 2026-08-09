# -*- coding: utf-8 -*-
"""資料來源：TWSE 全市場日成交報表（母體）+ shioaji-gateway 日K（策略計算用）。"""

import requests

GATEWAY = "http://localhost:5455"
TWSE_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"


def get_universe(n: int) -> list[dict]:
    """依前一交易日成交量排序，取上市股票前 n 檔。回傳 [{code, name, volume, close}, ...]。"""
    resp = requests.get(TWSE_ALL_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    rows = resp.json()

    stocks = []
    for row in rows:
        code = row.get("Code", "")
        if len(code) != 4 or not code.isdigit():
            continue
        try:
            close = float(row["ClosingPrice"])
            volume = int(row["TradeVolume"]) // 1000  # 股 → 張
        except (ValueError, KeyError):
            continue
        stocks.append({
            "code": code,
            "name": row.get("Name", code),
            "volume": volume,
            "close": close,
        })

    stocks.sort(key=lambda s: s["volume"], reverse=True)
    return stocks[:n]


def get_daily_bars(code: str, days: int = 95) -> list[dict]:
    """取單檔日K（舊到新）。失敗回傳空 list。"""
    try:
        resp = requests.get(f"{GATEWAY}/daily_ohlcv", params={"code": code, "days": days}, timeout=30)
        j = resp.json()
        return j.get("bars", []) if j.get("ok") else []
    except Exception:
        return []
