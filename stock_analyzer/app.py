#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify, request
import shioaji as sj
import pandas as pd
import yfinance as yf
import requests
from openai import OpenAI
from datetime import date, timedelta, datetime, timezone
import hashlib
import feedparser

app = Flask(__name__)

DEEPSEEK_API_KEY = "sk-49f9f0a651514aff96412fa7ad11ae85"
FINMIND_TOKEN = os.environ["FINMIND_TOKEN"]
DISCOUNT_RATE = 0.10
TERMINAL_GROWTH = 0.03

# ── Shioaji singleton ────────────────────────────────────────────
_sj_api = None

def _get_sj():
    global _sj_api
    if _sj_api is None:
        _sj_api = sj.Shioaji(simulation=False)
        _sj_api.login(
            api_key=os.environ["SHIOAJI_API_KEY"],
            secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        )
    return _sj_api


# ── 股票名稱對照表（中文搜尋用）────────────────────────────────
_stock_map = {}

def load_stock_map():
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
    q = query.strip()
    if q.isdigit():
        return q
    if q in _stock_map:
        return _stock_map[q]
    for name, code in _stock_map.items():
        if q in name:
            return code
    return q


# ── 技術面（yfinance 日K）────────────────────────────────────────
def analyze(symbol: str) -> dict:
    code = resolve_symbol(symbol)

    # 上市用 .TW，上櫃用 .TWO
    for suffix in [".TW", ".TWO"]:
        df = yf.download(f"{code}{suffix}", period="3mo", progress=False, auto_adjust=True)
        if not df.empty:
            break
    if df.empty:
        raise ValueError(f"找不到股票代碼 {code}")

    df.columns = [c[0].lower() for c in df.columns]  # 壓平多層 columns
    df = df.reset_index().rename(columns={"date": "date"})
    df.columns = [c.lower() for c in df.columns]
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    daily = df
    daily["volume"] = daily["volume"] / 1000  # 股 → 張

    latest = daily.iloc[-1]
    prev = daily.iloc[-2]

    price = round(float(latest["close"]), 2)
    open_price = round(float(latest["open"]), 2)
    high = round(float(latest["high"]), 2)
    low = round(float(latest["low"]), 2)
    volume = round(float(latest["volume"]), 0)
    prev_close = round(float(prev["close"]), 2)

    change = round(price - prev_close, 2)
    change_pct = round(change / prev_close * 100, 2)

    vol_5 = round(float(daily["volume"].tail(6).iloc[:-1].mean()), 0)
    vol_20 = round(float(daily["volume"].tail(21).iloc[:-1].mean()), 0)
    ma5 = round(float(daily["close"].tail(5).mean()), 2)
    ma20 = round(float(daily["close"].tail(20).mean()), 2)

    price_up = change > 0
    volume_vs_5 = volume / vol_5 if vol_5 else 1

    if price_up and volume_vs_5 >= 1.2:
        pattern, pattern_score, pattern_desc = "價漲量增 ✅", 85, "多方強勢，量能支撐，上漲機率較高"
    elif price_up:
        pattern, pattern_score, pattern_desc = "價漲量縮 ⚠️", 55, "上漲但量能不足，動能偏弱，需觀察"
    elif volume_vs_5 >= 1.2:
        pattern, pattern_score, pattern_desc = "價跌量增 ❌", 20, "下跌且放量，賣壓沉重，避免追入"
    else:
        pattern, pattern_score, pattern_desc = "價跌量縮 🔄", 45, "下跌縮量，跌勢可能趨緩，等待訊號"

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
        "volume": int(volume),
        "vol_5": int(vol_5),
        "vol_20": int(vol_20),
        "volume_ratio": round(volume_vs_5, 2),
        "ma5": ma5,
        "ma20": ma20,
        "ma_status": ma_status,
        "buy_pressure": None,
        "pattern": pattern,
        "pattern_score": pattern_score,
        "pattern_desc": pattern_desc,
        "note": "資料來源：永豐 Shioaji",
    }


# ── 基本面（FinMind）────────────────────────────────────────────
def _finmind(dataset: str, stock_id: str, start_date: str) -> list:
    r = requests.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={"dataset": dataset, "data_id": stock_id, "start_date": start_date, "token": FINMIND_TOKEN},
        timeout=15,
    )
    return r.json().get("data", [])


def fetch_fundamentals(code: str) -> dict:
    # 股票名稱 & 產業
    info_recs = _finmind("TaiwanStockInfo", code, "2020-01-01")
    info = next((r for r in info_recs if r["stock_id"] == code), {})
    name = info.get("stock_name") or code
    sector = info.get("industry_category") or "—"

    # EPS TTM（最近 4 季加總）
    fs_recs = _finmind("TaiwanStockFinancialStatements", code, str(date.today() - timedelta(days=400)))
    eps_list = sorted([r for r in fs_recs if r["type"] == "EPS"], key=lambda x: x["date"])
    ttm_eps = sum(r["value"] for r in eps_list[-4:]) if len(eps_list) >= 4 else (eps_list[-1]["value"] * 4 if eps_list else 0)

    # 營收成長率 YoY（最近 2 季 vs 去年同期 2 季）
    rev_list = sorted([r for r in fs_recs if r["type"] == "Revenue"], key=lambda x: x["date"])
    growth = 0.05
    if len(rev_list) >= 4:
        recent = sum(r["value"] for r in rev_list[-2:])
        prior = sum(r["value"] for r in rev_list[-4:-2])
        growth = max(min((recent - prior) / prior if prior else 0.05, 0.30), -0.05)

    # PE / PBR（最新交易日）
    per_recs = _finmind("TaiwanStockPER", code, str(date.today() - timedelta(days=5)))
    per_latest = per_recs[-1] if per_recs else {}
    pe = per_latest.get("PER")
    pbr = per_latest.get("PBR")

    # 現價（由 Shioaji analyze 取得，這裡用 Shioaji snapshot 補）
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks.get(code)
        snap = api.snapshots([contract])[0] if contract else None
        price = round(float(snap.close), 2) if snap else 0
    except:
        price = 0

    # DCF 估值
    dcf = 0.0
    if ttm_eps > 0:
        current_eps = float(ttm_eps)
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
        "market_cap": None,
        "eps": round(float(ttm_eps), 2),
        "growth": round(growth * 100, 1),
        "pe": round(float(pe), 1) if pe else None,
        "pbr": round(float(pbr), 2) if pbr else None,
        "roe": None,
        "price": price,
        "dcf": dcf,
        "upside": upside,
        "verdict": verdict,
    }


# ── 新聞面（Google News RSS + DeepSeek 情緒）────────────────────
def fetch_news(code: str, name: str, hours: int = 48) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    queries = [f"{name} {code}", name]
    articles = []
    seen = set()

    for query in queries:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        feed = feedparser.parse(url)
        for e in feed.entries:
            pub = e.get("published_parsed") or e.get("updated_parsed")
            if not pub:
                continue
            pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue
            aid = hashlib.md5(e.title.encode()).hexdigest()
            if aid in seen:
                continue
            seen.add(aid)
            age_h = int((datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600)
            age_str = f"{age_h}h前" if age_h < 24 else f"{age_h//24}天前"
            source = e.get("source", {}).get("title", "") or ""
            articles.append({
                "title": e.title,
                "url": e.link,
                "age": age_str,
                "source": source,
            })

    articles = articles[:10]

    if not articles:
        return {"articles": [], "sentiment": "無資料", "summary": "近 48 小時內未找到相關新聞。"}

    titles = "\n".join(f"- {a['title']}" for a in articles)
    prompt = f"""以下是台股 {name}（{code}）近 48 小時的新聞標題：

{titles}

請用繁體中文回答：
1. 整體情緒：偏多 / 偏空 / 中性（一個詞即可）
2. 摘要：2-3句話說明主要新聞方向與對股價可能的影響"""

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.choices[0].message.content

    sentiment = "中性"
    for kw in ["偏多", "偏空", "中性"]:
        if kw in raw:
            sentiment = kw
            break

    return {"articles": articles, "sentiment": sentiment, "summary": raw}


# ── Routes ───────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze")
def api_analyze():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        return jsonify(analyze(symbol))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fundamentals")
def api_fundamentals():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        return jsonify(fetch_fundamentals(code))
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
- EPS TTM：{fund['eps']} | 營收成長率：{fund['growth']}%
- 本益比：{fund['pe']} | 股價淨值比：{fund['pbr']}
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


@app.route("/api/news")
def api_news():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        # 從 FinMind 取中文名稱
        info_recs = _finmind("TaiwanStockInfo", code, "2020-01-01")
        info = next((r for r in info_recs if r["stock_id"] == code), {})
        name = info.get("stock_name") or code
        return jsonify(fetch_news(code, name))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5100, debug=True)
