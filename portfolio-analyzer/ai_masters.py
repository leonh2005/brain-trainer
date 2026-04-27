import os
import time
from openai import OpenAI

_cache = {"data": None, "time": 0}
CACHE_TTL = 3600

MASTERS = {
    "buffett": {
        "name": "Warren Buffett",
        "title": "巴菲特",
        "prompt": "You are Warren Buffett. Analyze this portfolio: focus on economic moats, long-term competitive advantages, quality of earnings, return on equity, and whether you'd hold each position for 10+ years. Be direct and plain-spoken. Reply in Traditional Chinese (繁體中文)."
    },
    "lynch": {
        "name": "Peter Lynch",
        "title": "彼得·林奇",
        "prompt": "You are Peter Lynch. Analyze this portfolio: focus on growth potential, PEG ratios, whether these are businesses you can understand, and identify any 'ten-baggers'. Be enthusiastic but grounded. Reply in Traditional Chinese (繁體中文)."
    },
    "graham": {
        "name": "Benjamin Graham",
        "title": "葛雷厄姆",
        "prompt": "You are Benjamin Graham. Analyze this portfolio: focus on margin of safety, intrinsic value vs market price, balance sheet strength, and whether these qualify as defensive or enterprising investments. Be methodical and conservative. Reply in Traditional Chinese (繁體中文)."
    },
    "dalio": {
        "name": "Ray Dalio",
        "title": "雷·達利歐",
        "prompt": "You are Ray Dalio. Analyze this portfolio from your All Weather perspective: focus on correlation between assets, exposure to different economic environments (growth/recession/inflation/deflation), and whether the portfolio is truly diversified across risk factors. Reply in Traditional Chinese (繁體中文)."
    }
}


def build_portfolio_summary(portfolio_data: dict) -> str:
    positions = portfolio_data["positions"]
    metrics = portfolio_data["metrics"]
    lines = [
        f"Portfolio Total: NT${portfolio_data['total_twd']:,.0f} (≈ US${portfolio_data['total_usd']:,.0f})",
        f"Sharpe Ratio: {metrics['sharpe']} | VaR 95%: {metrics['var_95']}% | Max Drawdown: {metrics['max_drawdown']}%",
        f"Annual Return (1Y): {metrics['annual_return']}% | Volatility: {metrics['annual_vol']}%",
        "",
        "Current Holdings:"
    ]
    for p in positions:
        ticker = p["ticker"].replace(".TW", "").replace(".TWO", "")
        lines.append(f"  {ticker} ({p['name']}): NT${p['value_twd']:,.0f} ({p['weight']}%)")
    lines += [
        "",
        "Target Allocation:",
        "  VWRA 30% | 0050 15% | 00881 10% | 00864B 10% | Gold 10% | GRID 12.5% | XLU 12.5%"
    ]
    return "\n".join(lines)


def get_master_analysis(master_key: str, portfolio_data: dict) -> str:
    master = MASTERS[master_key]
    summary = build_portfolio_summary(portfolio_data)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=800,
        messages=[
            {"role": "system", "content": master["prompt"]},
            {"role": "user", "content": f"Analyze my portfolio and comment on the transition plan:\n\n{summary}"}
        ]
    )
    return response.choices[0].message.content


def get_all_analyses(portfolio_data: dict, force_refresh: bool = False) -> dict:
    global _cache
    now = time.time()
    if not force_refresh and _cache["data"] and now - _cache["time"] < CACHE_TTL:
        return _cache["data"]

    results = {}
    for key, master in MASTERS.items():
        try:
            results[key] = {
                "title": master["title"],
                "name": master["name"],
                "analysis": get_master_analysis(key, portfolio_data),
                "error": None
            }
        except Exception as e:
            results[key] = {
                "title": master["title"],
                "name": master["name"],
                "analysis": None,
                "error": str(e)
            }

    _cache = {"data": results, "time": now}
    return results
