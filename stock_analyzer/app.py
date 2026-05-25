#!/usr/bin/env python3
import os
from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import requests
from openai import OpenAI

app = Flask(__name__)

DEEPSEEK_API_KEY = "sk-49f9f0a651514aff96412fa7ad11ae85"
DISCOUNT_RATE = 0.10
TERMINAL_GROWTH = 0.03
_stock_map = {}  # 名稱 -> 代碼

def load_stock_map():
    """從 TWSE + TPEX 載入股票名稱對照表"""
    global _stock_map
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=5, verify=False)
        for s in r.json():
            _stock_map[s.get("Name", "").strip()] = s.get("Code", "").strip()
    except:
        pass
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", timeout=5, verify=False)
        for s in r.json():
            _stock_map[s.get("CompanyName", "").strip()] = s.get("SecuritiesCompanyCode", "").strip()
    except:
        pass

load_stock_map()

def resolve_symbol(query: str) -> str:
    """將中文名稱或代碼轉成代碼"""
    q = query.strip()
    if q.isdigit():
        return q
    # 完全匹配
    if q in _stock_map:
        return _stock_map[q]
    # 部分匹配
    for name, code in _stock_map.items():
        if q in name:
            return code
    return q  # 找不到就原樣回傳


def analyze(symbol: str) -> dict:
    code = resolve_symbol(symbol)
    ticker_code = code + ".TW" if not code.endswith(".TW") else code

    tk = yf.Ticker(ticker_code)
    hist = tk.history(period="3mo", interval="1d")

    if hist.empty:
        # 嘗試上櫃
        ticker_code = code + ".TWO"
        tk = yf.Ticker(ticker_code)
        hist = tk.history(period="3mo", interval="1d")

    if hist.empty:
        raise ValueError(f"找不到股票代碼 {code}，請確認是否正確")

    latest = hist.iloc[-1]
    prev = hist.iloc[-2]

    price = round(float(latest["Close"]), 2)
    open_price = round(float(latest["Open"]), 2)
    high = round(float(latest["High"]), 2)
    low = round(float(latest["Low"]), 2)
    volume = int(latest["Volume"])
    prev_close = round(float(prev["Close"]), 2)

    change = round(price - prev_close, 2)
    change_pct = round(change / prev_close * 100, 2)

    # 5日均量、20日均量
    vol_5 = int(hist["Volume"].tail(6).iloc[:-1].mean())
    vol_20 = int(hist["Volume"].tail(21).iloc[:-1].mean())

    # 均線
    ma5 = round(hist["Close"].tail(5).mean(), 2)
    ma20 = round(hist["Close"].tail(20).mean(), 2)

    # 量價型態
    price_up = change > 0
    volume_vs_5 = volume / vol_5 if vol_5 else 1

    if price_up and volume_vs_5 >= 1.2:
        pattern = "價漲量增 ✅"
        pattern_score = 85
        pattern_desc = "多方強勢，量能支撐，上漲機率較高"
    elif price_up and volume_vs_5 < 1.2:
        pattern = "價漲量縮 ⚠️"
        pattern_score = 55
        pattern_desc = "上漲但量能不足，動能偏弱，需觀察"
    elif not price_up and volume_vs_5 >= 1.2:
        pattern = "價跌量增 ❌"
        pattern_score = 20
        pattern_desc = "下跌且放量，賣壓沉重，避免追入"
    else:
        pattern = "價跌量縮 🔄"
        pattern_score = 45
        pattern_desc = "下跌縮量，跌勢可能趨緩，等待訊號"

    # 均線加分
    above_ma5 = price > ma5
    above_ma20 = price > ma20
    if above_ma5 and above_ma20:
        pattern_score = min(pattern_score + 10, 95)
        ma_status = f"站上 MA5({ma5}) & MA20({ma20}) ✅"
    elif above_ma5:
        pattern_score = min(pattern_score + 5, 95)
        ma_status = f"站上 MA5({ma5})，MA20({ma20}) 待突破"
    else:
        pattern_score = max(pattern_score - 5, 5)
        ma_status = f"MA5({ma5}) & MA20({ma20}) 均線下方 ⚠️"

    return {
        "symbol": code,
        "price": price,
        "open": open_price,
        "high": high,
        "low": low,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
        "vol_5": vol_5,
        "vol_20": vol_20,
        "volume_ratio": round(volume_vs_5, 2),
        "ma5": ma5,
        "ma20": ma20,
        "ma_status": ma_status,
        "buy_pressure": None,
        "pattern": pattern,
        "pattern_score": pattern_score,
        "pattern_desc": pattern_desc,
        "note": "資料延遲約 15 分鐘（yfinance）",
    }


def fetch_fundamentals(code: str) -> dict:
    ticker = code + ".TW"
    tk = yf.Ticker(ticker)
    info = tk.info
    if not info.get("currentPrice"):
        ticker = code + ".TWO"
        tk = yf.Ticker(ticker)
        info = tk.info

    eps = info.get("trailingEps") or info.get("epsTrailingTwelveMonths") or 0
    growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.05
    growth = max(min(float(growth), 0.30), -0.05)
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    pe = info.get("trailingPE") or info.get("forwardPE") or 0
    roe = info.get("returnOnEquity") or 0
    name = info.get("longName") or info.get("shortName") or code
    sector = info.get("sector") or "—"
    market_cap = info.get("marketCap") or 0

    dcf = 0.0
    if eps > 0:
        current_eps = float(eps)
        pv = 0.0
        high_g = growth
        low_g = (growth + TERMINAL_GROWTH) / 2
        for i in range(1, 11):
            g = high_g if i <= 5 else low_g
            current_eps *= (1 + g)
            pv += current_eps / (1 + DISCOUNT_RATE) ** i
        terminal_eps = current_eps * (1 + TERMINAL_GROWTH)
        terminal_value = terminal_eps / (DISCOUNT_RATE - TERMINAL_GROWTH)
        pv += terminal_value / (1 + DISCOUNT_RATE) ** 10
        dcf = round(pv, 2)

    upside = None
    verdict = "資料不足"
    if dcf > 0 and price > 0:
        upside = round((dcf - price) / price * 100, 1)
        verdict = "低估 ▲" if upside > 20 else ("高估 ▼" if upside < -20 else "合理區間")

    return {
        "name": name,
        "sector": sector,
        "market_cap": market_cap,
        "eps": round(float(eps), 2),
        "growth": round(growth * 100, 1),
        "pe": round(float(pe), 1) if pe else None,
        "roe": round(float(roe) * 100, 1) if roe else None,
        "price": round(float(price), 2),
        "dcf": dcf,
        "upside": upside,
        "verdict": verdict,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze")
def api_analyze():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        result = analyze(symbol)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai-analysis")
def api_ai_analysis():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        tech = analyze(code)
        fund = fetch_fundamentals(code)

        prompt = f"""你是台股專業分析師，請針對以下個股給出簡潔的買賣建議（繁體中文，200字以內）：

股票：{fund['name']}（{code}）
產業：{fund['sector']}

【技術面】
- 收盤：{tech['price']} | 漲跌：{tech['change_pct']}%
- 量價型態：{tech['pattern']}（{tech['pattern_desc']}）
- 均線：{tech['ma_status']}
- 技術評分：{tech['pattern_score']}/100

【基本面】
- EPS：{fund['eps']} | 預估成長率：{fund['growth']}%
- 本益比：{fund['pe']} | ROE：{fund['roe']}%
- DCF 估值：{fund['dcf']} | 現價：{fund['price']} → {fund['verdict']}（溢/折價 {fund['upside']}%）

請給出：1) 整體評估（買入/觀察/避開）2) 主要理由（技術+基本面各一點）3) 注意風險"""

        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"analysis": resp.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fundamentals")
def api_fundamentals():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        result = fetch_fundamentals(code)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5100, debug=False)
