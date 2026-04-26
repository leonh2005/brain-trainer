import yfinance as yf
import numpy as np
import json
import time
from pathlib import Path

PORTFOLIO_FILE = Path(__file__).parent / "portfolio.json"
CACHE_TTL = 900  # 15 分鐘快取
_cache = {"data": None, "time": 0}


def load_portfolio():
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def fetch_data(portfolio):
    tw = [p["ticker"] for p in portfolio["taiwan"]]
    us = [p["ticker"] for p in portfolio["us"]]
    all_tickers = tw + us + ["USDTWD=X"]

    raw = yf.download(all_tickers, period="1y", auto_adjust=True, progress=False)["Close"]
    # 確保是 DataFrame
    if hasattr(raw, "squeeze") and raw.ndim == 1:
        raw = raw.to_frame(name=all_tickers[0])

    usdtwd = float(raw["USDTWD=X"].dropna().iloc[-1])
    prices = {col: float(raw[col].dropna().iloc[-1]) if not raw[col].dropna().empty else float("nan")
              for col in raw.columns}
    return raw.drop(columns=["USDTWD=X"], errors="ignore"), prices, usdtwd


def build_positions(portfolio, prices, usdtwd):
    positions = []

    for p in portfolio["taiwan"]:
        price = float(prices.get(p["ticker"], 0) or 0)
        value = price * p["shares"]
        positions.append({**p, "price": round(price, 2), "value_twd": round(value, 0), "currency": "TWD"})

    for p in portfolio["us"]:
        price = float(prices.get(p["ticker"], 0) or 0)
        value_usd = price * p["shares"]
        value_twd = value_usd * usdtwd
        positions.append({
            **p,
            "price": round(price, 2),
            "value_usd": round(value_usd, 2),
            "value_twd": round(value_twd, 0),
            "currency": "USD"
        })

    total = sum(p["value_twd"] for p in positions)
    for p in positions:
        p["weight"] = round(p["value_twd"] / total * 100, 2) if total > 0 else 0

    return positions, total


def calculate_metrics(hist, positions):
    tickers = [p["ticker"] for p in positions]
    weights_map = {p["ticker"]: p["weight"] / 100 for p in positions}

    cols = [t for t in tickers if t in hist.columns]
    returns = hist[cols].pct_change().dropna()

    w = np.array([weights_map.get(c, 0) for c in cols])
    if w.sum() > 0:
        w = w / w.sum()

    port_ret = returns.values.dot(w)

    var_95 = float(np.percentile(port_ret, 5))
    var_99 = float(np.percentile(port_ret, 1))

    annual_ret = float(port_ret.mean() * 252)
    annual_vol = float(port_ret.std() * np.sqrt(252))
    sharpe = (annual_ret - 0.015) / annual_vol if annual_vol > 0 else 0

    cum = (1 + port_ret).cumprod()
    roll_max = np.maximum.accumulate(cum)
    max_dd = float(((cum - roll_max) / roll_max).min())

    corr = returns.corr().round(3)

    return {
        "var_95": round(var_95 * 100, 2),
        "var_99": round(var_99 * 100, 2),
        "sharpe": round(sharpe, 3),
        "annual_return": round(annual_ret * 100, 2),
        "annual_vol": round(annual_vol * 100, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "correlation": corr.to_dict()
    }


def get_portfolio_data(force_refresh=False):
    global _cache
    now = time.time()
    if not force_refresh and _cache["data"] and now - _cache["time"] < CACHE_TTL:
        return _cache["data"]

    portfolio = load_portfolio()
    hist, prices, usdtwd = fetch_data(portfolio)
    positions, total = build_positions(portfolio, prices, usdtwd)
    metrics = calculate_metrics(hist, positions)

    data = {
        "positions": positions,
        "total_twd": round(total, 0),
        "total_usd": round(total / usdtwd, 0),
        "usdtwd": round(usdtwd, 2),
        "metrics": metrics,
        "target": portfolio.get("target", []),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    _cache = {"data": data, "time": now}
    return data
