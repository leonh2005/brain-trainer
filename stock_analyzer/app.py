#!/usr/bin/env python3
import os
from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import requests

app = Flask(__name__)
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


if __name__ == "__main__":
    app.run(port=5001, debug=False)
