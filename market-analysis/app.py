"""市場分析單頁儀表板 Flask 服務 (port 5350)

/api/analysis -> analysis_data.json 內容
/api/live     -> 大盤+六檔權值股即時報價 (Shioaji 優先, yfinance fallback, 30秒快取)
"""
import json
import os
import time

from flask import Flask, jsonify, render_template

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_JSON = os.path.join(BASE_DIR, "analysis_data.json")
SECRETS_DIR = "/Users/steven/CCProject/.secrets"

STOCKS = [
    {"code": "2330", "name": "台積電"},
    {"code": "2454", "name": "聯發科"},
    {"code": "2308", "name": "台達電"},
    {"code": "2317", "name": "鴻海"},
    {"code": "3711", "name": "日月光"},
    {"code": "2382", "name": "廣達"},
]

_live_cache = {"data": None, "ts": 0}
CACHE_TTL = 30


def _read_secret(filename):
    path = os.path.join(SECRETS_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _fetch_via_shioaji():
    import shioaji as sj

    api_key = os.environ.get("SHIOAJI_API_KEY") or _read_secret("shioaji_api.txt")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY") or _read_secret("shioaji_secret.txt")

    api = sj.Shioaji()
    try:
        api.login(api_key=api_key, secret_key=secret_key)

        index_contract = api.Contracts.Indexs.TSE["IX0001"]
        idx_snap = api.snapshots([index_contract])[0]
        index_close = idx_snap.close
        index_ref = index_close - idx_snap.change_price
        index_pct = (
            round(idx_snap.change_rate, 2)
            if idx_snap.change_rate
            else round((index_close - index_ref) / index_ref * 100, 2)
        )

        stocks_out = []
        contracts = [api.Contracts.Stocks[s["code"]] for s in STOCKS]
        snaps = api.snapshots(contracts)
        snap_by_code = {s.code: s for s in snaps}
        for s in STOCKS:
            snap = snap_by_code.get(s["code"])
            if snap is None:
                continue
            ref = snap.close - snap.change_price
            pct = round(snap.change_rate, 2) if snap.change_rate else (
                round((snap.close - ref) / ref * 100, 2) if ref else 0.0
            )
            stocks_out.append(
                {
                    "code": s["code"],
                    "name": s["name"],
                    "price": snap.close,
                    "change_pct": pct,
                }
            )

        return {
            "index": {
                "price": index_close,
                "change_pct": index_pct,
                "change_point": round(idx_snap.change_price, 2),
            },
            "stocks": stocks_out,
            "source": "shioaji",
        }
    finally:
        try:
            api.logout()
        except Exception:
            pass


def _fetch_via_yfinance():
    import yfinance as yf

    def last_two_closes(ticker):
        hist = yf.Ticker(ticker).history(period="5d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None, None
        return closes.iloc[-1], closes.iloc[-2]

    index_close, index_prev = last_two_closes("^TWII")
    index_pct = (
        round((index_close - index_prev) / index_prev * 100, 2)
        if index_close and index_prev
        else None
    )

    stocks_out = []
    for s in STOCKS:
        close, prev = last_two_closes(f"{s['code']}.TW")
        if close is None:
            continue
        pct = round((close - prev) / prev * 100, 2)
        stocks_out.append(
            {"code": s["code"], "name": s["name"], "price": round(close, 2), "change_pct": pct}
        )

    return {
        "index": {
            "price": round(index_close, 2) if index_close else None,
            "change_pct": index_pct,
            "change_point": round(index_close - index_prev, 2) if (index_close and index_prev) else None,
        },
        "stocks": stocks_out,
        "source": "yfinance",
    }


def _regional_indices():
    """日股(日經225)、韓股(KOSPI) 大盤，走 yfinance(Shioaji 無海外指數)。"""
    import yfinance as yf
    out = []
    for code, name, ticker in [("N225", "日經225", "^N225"), ("KOSPI", "韓股KOSPI", "^KS11")]:
        try:
            closes = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
            if len(closes) < 2:
                continue
            close, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            out.append({
                "code": code,
                "name": name,
                "price": round(close, 2),
                "change_pct": round((close - prev) / prev * 100, 2),
                "change_point": round(close - prev, 2),
            })
        except Exception:
            continue
    return out


def _fetch_live():
    now = time.time()
    if _live_cache["data"] is not None and now - _live_cache["ts"] < CACHE_TTL:
        return _live_cache["data"]

    try:
        result = _fetch_via_shioaji()
    except Exception:
        try:
            result = _fetch_via_yfinance()
        except Exception as e:
            result = {"ok": False, "error": str(e)}
            _live_cache["data"] = result
            _live_cache["ts"] = now
            return result

    try:
        result["regional"] = _regional_indices()
    except Exception:
        result["regional"] = []
    result["ok"] = True
    result["updated"] = time.strftime("%H:%M:%S")
    _live_cache["data"] = result
    _live_cache["ts"] = now
    return result


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/analysis")
def analysis():
    with open(ANALYSIS_JSON, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.get("/api/live")
def live():
    try:
        return jsonify(_fetch_live())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5350, debug=False)
