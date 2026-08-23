import yfinance as yf
import time

_cache = {"data": None, "time": 0}
CACHE_TTL = 3600

# 不做 DCF 的標的（ETF、債券）
SKIP_DCF = {"00687B.TWO", "00937B.TWO"}

# CAPM / WACC 參數（台股適用的簡化常數）
RISK_FREE_RATE = 0.015      # 台灣10年公債殖利率約略值
MARKET_RISK_PREMIUM = 0.06  # 股權風險溢酬
DEBT_CREDIT_SPREAD = 0.02   # 舉債利率 = 無風險利率 + 信用加碼（無明細利率時的近似）
TAX_RATE = 0.20             # 台灣營所稅

TERMINAL_GROWTH = 0.03   # 永續成長率（基準情境）
YEARS = 10               # 預測年數

# 敏感度分析：折現率 / 永續成長率 各自上下調整的幅度
DISCOUNT_SENSITIVITY = 0.015
GROWTH_SENSITIVITY = 0.01


def fetch_fundamentals(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info

    eps = info.get("trailingEps") or info.get("epsTrailingTwelveMonths") or 0

    growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.05
    growth = max(min(growth, 0.30), -0.05)  # 限制在 -5% ~ 30%

    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    pe = info.get("trailingPE") or info.get("forwardPE") or 15

    free_cash_flow = info.get("freeCashflow") or 0
    operating_cash_flow = info.get("operatingCashflow") or 0
    if free_cash_flow <= 0 and operating_cash_flow > 0:
        # 抓不到自由現金流時，用營業現金流估算（假設資本支出約占營業現金流 30%）
        free_cash_flow = operating_cash_flow * 0.7

    total_debt = info.get("totalDebt") or 0
    total_cash = info.get("totalCash") or 0
    shares_outstanding = info.get("sharesOutstanding") or 0
    market_cap = info.get("marketCap") or 0
    beta = info.get("beta") or 1.0

    return {
        "eps": eps,
        "growth": growth,
        "price": price,
        "pe": pe,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "—",
        "roe": info.get("returnOnEquity") or 0,
        "debt_equity": info.get("debtToEquity") or 0,
        "free_cash_flow": free_cash_flow,
        "total_debt": total_debt,
        "total_cash": total_cash,
        "shares_outstanding": shares_outstanding,
        "market_cap": market_cap,
        "beta": beta,
    }


def calculate_wacc(market_cap: float, total_debt: float, beta: float) -> float:
    """CAPM 算股權成本，依市值/負債權重混合舉債成本，算出風險調整後折現率。"""
    cost_of_equity = RISK_FREE_RATE + beta * MARKET_RISK_PREMIUM
    cost_of_debt_after_tax = (RISK_FREE_RATE + DEBT_CREDIT_SPREAD) * (1 - TAX_RATE)

    total_value = market_cap + total_debt
    if total_value <= 0:
        return cost_of_equity

    equity_weight = market_cap / total_value
    debt_weight = total_debt / total_value
    return equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax


def _project_enterprise_value(fcf: float, growth: float, discount: float,
                               terminal: float, years: int = YEARS) -> float:
    """兩段式現金流折現：前半段用預估成長率，後半段線性收斂至永續成長率，
    折現後加終值（Gordon Growth Model），得企業價值（Enterprise Value）。"""
    if fcf <= 0 or discount <= terminal:
        return 0.0

    half = years // 2
    high_growth = growth
    low_growth = (growth + terminal) / 2

    pv = 0.0
    current_fcf = fcf
    for i in range(1, years + 1):
        g = high_growth if i <= half else low_growth
        current_fcf *= (1 + g)
        pv += current_fcf / (1 + discount) ** i

    terminal_fcf = current_fcf * (1 + terminal)
    terminal_value = terminal_fcf / (discount - terminal)
    pv += terminal_value / (1 + discount) ** years

    return pv


def calculate_dcf_per_share(f: dict, discount: float, terminal: float) -> float:
    """企業價值 → 扣淨負債 → 股東權益價值 → 除以股數 = 每股內在價值。"""
    if f["shares_outstanding"] <= 0:
        return 0.0
    ev = _project_enterprise_value(f["free_cash_flow"], f["growth"], discount, terminal)
    if ev <= 0:
        return 0.0
    net_debt = f["total_debt"] - f["total_cash"]
    equity_value = ev - net_debt
    return max(equity_value / f["shares_outstanding"], 0.0)


def get_dcf_data(positions: list, force_refresh: bool = False) -> list:
    global _cache
    now = time.time()
    if not force_refresh and _cache["data"] and now - _cache["time"] < CACHE_TTL:
        return _cache["data"]

    results = []
    for p in positions:
        ticker = p["ticker"]
        name = p["name"]

        if ticker in SKIP_DCF or ticker.endswith(".TWO"):
            results.append({
                "ticker": ticker,
                "name": name,
                "type": "ETF/債券",
                "dcf_low": None, "dcf_mid": None, "dcf_high": None,
                "price": p.get("price", 0),
                "upside": None,
                "verdict": "—",
                "eps": None,
                "growth": None,
                "roe": None,
                "pe": None,
                "wacc": None,
                "error": None,
            })
            continue

        try:
            f = fetch_fundamentals(ticker)
            wacc = calculate_wacc(f["market_cap"], f["total_debt"], f["beta"])

            # 敏感度分析：悲觀（折現率+、成長率-）／基準／樂觀（折現率-、成長率+）
            dcf_low = calculate_dcf_per_share(f, wacc + DISCOUNT_SENSITIVITY, TERMINAL_GROWTH - GROWTH_SENSITIVITY)
            dcf_mid = calculate_dcf_per_share(f, wacc, TERMINAL_GROWTH)
            dcf_high = calculate_dcf_per_share(f, wacc - DISCOUNT_SENSITIVITY, TERMINAL_GROWTH + GROWTH_SENSITIVITY)

            if dcf_mid > 0 and f["price"] > 0:
                upside = (dcf_mid - f["price"]) / f["price"] * 100
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
                "dcf_low": round(dcf_low, 2) if dcf_low else None,
                "dcf_mid": round(dcf_mid, 2) if dcf_mid else None,
                "dcf_high": round(dcf_high, 2) if dcf_high else None,
                "price": round(f["price"], 2),
                "upside": round(upside, 1) if upside is not None else None,
                "verdict": verdict,
                "eps": round(f["eps"], 2),
                "growth": round(f["growth"] * 100, 1),
                "roe": round(f["roe"] * 100, 1) if f["roe"] else None,
                "pe": round(f["pe"], 1) if f["pe"] else None,
                "wacc": round(wacc * 100, 1),
                "error": None,
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "name": name,
                "type": "股票",
                "dcf_low": None, "dcf_mid": None, "dcf_high": None,
                "price": p.get("price", 0),
                "upside": None,
                "verdict": "錯誤",
                "eps": None,
                "growth": None,
                "roe": None,
                "pe": None,
                "wacc": None,
                "error": str(e)[:80],
            })

    _cache = {"data": results, "time": now}
    return results
