"""市場分析單頁儀表板 Flask 服務 (port 5350)

/api/analysis -> analysis_data.json 內容
/api/live     -> 大盤+六檔權值股即時報價 (Shioaji 優先, yfinance fallback, 30秒快取)
"""
import json
import os
import time

from flask import Flask, jsonify, render_template, request

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


SHIOAJI_GATEWAY = "http://127.0.0.1:5455"


def _fetch_via_shioaji():
    """改由 shioaji-gateway(單一共用連線)取報價；gateway 異常時往上拋，交給 yfinance fallback。"""
    import urllib.request
    codes = "IX0001," + ",".join(s["code"] for s in STOCKS)
    with urllib.request.urlopen(f"{SHIOAJI_GATEWAY}/snapshot?codes={codes}", timeout=15) as r:
        resp = json.loads(r.read())
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "gateway error"))
    data = resp["data"]

    idx = data["IX0001"]
    index_close = idx["close"]
    index_ref = index_close - idx["change_price"]
    index_pct = idx["change_rate"] if idx["change_rate"] is not None else (
        round((index_close - index_ref) / index_ref * 100, 2) if index_ref else 0.0
    )

    stocks_out = []
    for s in STOCKS:
        d = data.get(s["code"])
        if d is None:
            continue
        ref = d["close"] - d["change_price"]
        pct = d["change_rate"] if d["change_rate"] is not None else (
            round((d["close"] - ref) / ref * 100, 2) if ref else 0.0
        )
        stocks_out.append({"code": s["code"], "name": s["name"], "price": d["close"],
                           "change_pct": pct, "change_point": round(d["change_price"], 2)})

    return {
        "index": {
            "price": index_close,
            "change_pct": round(index_pct, 2),
            "change_point": round(idx["change_price"], 2),
        },
        "stocks": stocks_out,
        "source": "shioaji",
    }


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
            {"code": s["code"], "name": s["name"], "price": round(close, 2),
             "change_pct": pct, "change_point": round(close - prev, 2)}
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


def _fetch_live(force=False):
    now = time.time()
    if not force and _live_cache["data"] is not None and now - _live_cache["ts"] < CACHE_TTL:
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
        return jsonify(_fetch_live(force=request.args.get("fresh") == "1"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/econ")
def econ_api():
    try:
        import econ
        return jsonify({"ok": True, "data": econ.get_econ(force=request.args.get("fresh") == "1")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5350, debug=False)
