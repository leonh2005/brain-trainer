import yfinance as yf
import numpy as np
import time

_cache = {"data": None, "time": 0}
CACHE_TTL = 3600

# 不做 DCF 的標的（ETF、債券）
SKIP_DCF = {"00687B.TWO", "00937B.TWO"}

# DCF 參數
DISCOUNT_RATE = 0.10     # 折現率 10%
TERMINAL_GROWTH = 0.03   # 永續成長率 3%
YEARS = 10               # 預測年數


def fetch_fundamentals(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info

    # 每股盈餘
    eps = info.get("trailingEps") or info.get("epsTrailingTwelveMonths") or 0

    # 預估成長率（優先用分析師預估，否則用歷史）
    growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.05
    growth = max(min(growth, 0.30), -0.05)  # 限制在 -5% ~ 30%

    # 現價
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

    # 本益比
    pe = info.get("trailingPE") or info.get("forwardPE") or 15

    return {
        "eps": eps,
        "growth": growth,
        "price": price,
        "pe": pe,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "—",
        "roe": info.get("returnOnEquity") or 0,
        "debt_equity": info.get("debtToEquity") or 0,
    }


def calculate_dcf(eps: float, growth: float, discount: float = DISCOUNT_RATE,
                  terminal: float = TERMINAL_GROWTH, years: int = YEARS,
                  exit_pe: float = 15) -> float:
    if eps <= 0:
        return 0.0

    # 兩段式 DCF：前 5 年高成長，後 5 年線性收斂至永續成長
    half = years // 2
    high_growth = growth
    low_growth = (growth + terminal) / 2

    pv = 0.0
    current_eps = eps
    for i in range(1, years + 1):
        g = high_growth if i <= half else low_growth
        current_eps *= (1 + g)
        pv += current_eps / (1 + discount) ** i

    # 終端價值（用 Gordon Growth Model）
    terminal_eps = current_eps * (1 + terminal)
    terminal_value = terminal_eps / (discount - terminal)
    pv += terminal_value / (1 + discount) ** years

    return round(pv, 2)


def get_dcf_data(positions: list, force_refresh: bool = False) -> list:
    global _cache
    now = time.time()
    if not force_refresh and _cache["data"] and now - _cache["time"] < CACHE_TTL:
        return _cache["data"]

    results = []
    for p in positions:
        ticker = p["ticker"]
        name = p["name"]

        # ETF / 債券跳過 DCF，直接標記
        if ticker in SKIP_DCF or ticker.endswith(".TWO"):
            results.append({
                "ticker": ticker,
                "name": name,
                "type": "ETF/債券",
                "dcf": None,
                "price": p.get("price", 0),
                "upside": None,
                "verdict": "—",
                "eps": None,
                "growth": None,
                "roe": None,
                "pe": None,
                "error": None,
            })
            continue

        try:
            f = fetch_fundamentals(ticker)
            dcf_value = calculate_dcf(f["eps"], f["growth"])

            if dcf_value > 0 and f["price"] > 0:
                upside = (dcf_value - f["price"]) / f["price"] * 100
                if upside > 20:
                    verdict = "低估 ▲"
                elif upside < -20:
                    verdict = "高估 ▼"
                else:
                    verdict = "合理"
            else:
                upside = None
                verdict = "資料不足"

            results.append({
                "ticker": ticker,
                "name": name,
                "type": "股票",
                "dcf": round(dcf_value, 2),
                "price": round(f["price"], 2),
                "upside": round(upside, 1) if upside is not None else None,
                "verdict": verdict,
                "eps": round(f["eps"], 2),
                "growth": round(f["growth"] * 100, 1),
                "roe": round(f["roe"] * 100, 1) if f["roe"] else None,
                "pe": round(f["pe"], 1) if f["pe"] else None,
                "error": None,
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "name": name,
                "type": "股票",
                "dcf": None,
                "price": p.get("price", 0),
                "upside": None,
                "verdict": "錯誤",
                "eps": None,
                "growth": None,
                "roe": None,
                "pe": None,
                "error": str(e)[:80],
            })

    _cache = {"data": results, "time": now}
    return results
