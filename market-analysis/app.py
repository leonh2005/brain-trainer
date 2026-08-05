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

_intraday_cache = {"data": None, "ts": 0}
_regional_intraday_cache = {"data": None, "ts": 0}
_stocks_intraday_cache = {"data": None, "ts": 0}
INTRADAY_CACHE_TTL = 55  # 前端每 60 秒刷新一次，快取略短於刷新間隔

_volume_cache = {"data": None, "ts": 0}
VOLUME_CACHE_TTL = 300  # 盤中每 5 分鐘更新一次估算值


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
            "_total_amount": idx.get("total_amount"),
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


def _oil_price():
    """WTI + UKOIL(布蘭特原油) 現價，走 yfinance。"""
    import yfinance as yf

    out = []
    for ticker, name in (("CL=F", "WTI原油"), ("BZ=F", "UKOIL布蘭特")):
        try:
            closes = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
            if len(closes) < 2:
                continue
            close, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            out.append({
                "name": name, "price": round(close, 2),
                "change_pct": round((close - prev) / prev * 100, 2),
                "change_point": round(close - prev, 2),
            })
        except Exception:
            continue
    return out or None


def _fetch_fmtqik(date_str=None):
    """抓 TWSE 單月成交金額列表（date_str 格式 YYYYMMDD，代表該月）。"""
    import urllib.request

    url = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"
    if date_str:
        url += f"&date={date_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        fm = json.loads(r.read())
    return fm.get("data") or []


def _recent_closed_days_avg(n=5):
    """取最近 n 個「已收盤」交易日的成交金額均值（億元），自動跨月補齊，排除今天。"""
    import datetime

    today = datetime.date.today()
    today_roc = f"{today.year - 1911}/{today.month:02d}/{today.day:02d}"

    rows = _fetch_fmtqik()
    if rows and rows[-1][0] == today_roc:
        rows = rows[:-1]  # 今天尚未收盤，排除
    if len(rows) < n:
        prev_month_last_day = today.replace(day=1) - datetime.timedelta(days=1)
        rows = _fetch_fmtqik(prev_month_last_day.strftime("%Y%m01")) + rows

    tail = rows[-n:]
    if not tail:
        return None
    values = [float(row[2].replace(",", "")) / 1e8 for row in tail]  # 元 -> 億元
    return round(sum(values) / len(values), 1)


def _fetch_volume_stats(today_amount=None, force=False):
    """大盤量能：用當下即時累計成交金額（Shioaji total_amount，交易時間內持續更新）
    依「已過交易時間比例」外推估算全日成交金額，跟近5個交易日均量比較，判斷量增/量縮。

    today_amount: 今日截至目前累計成交金額（元），由呼叫端從即時報價（Shioaji）帶入。
    這是估算值，非官方公布的全日數字——收盤前的估算會隨時間推移持續修正。
    """
    now = time.time()
    if not force and _volume_cache["data"] is not None and now - _volume_cache["ts"] < VOLUME_CACHE_TTL:
        return _volume_cache["data"]

    result = {"est_full_day_100m": None, "avg5_100m": None, "vol_ratio": None,
              "vol_diff_100m": None, "vol_diff_pct": None, "as_of": None, "error": None}
    try:
        if today_amount:
            acc_value_100m = float(today_amount) / 1e8  # 元 -> 億元
            now_local = time.localtime()
            elapsed_min = (now_local.tm_hour * 60 + now_local.tm_min) - 9 * 60  # 09:00 開盤
            elapsed_min = max(1, min(elapsed_min, 270))  # 09:00–13:30 共 270 分鐘
            result["est_full_day_100m"] = round(acc_value_100m / elapsed_min * 270, 1)
            result["as_of"] = time.strftime("%H:%M")
        else:
            result["error"] = "今日尚無成交資料（開盤前或即時來源不可用）"

        result["avg5_100m"] = _recent_closed_days_avg(5)

        if result["est_full_day_100m"] and result["avg5_100m"]:
            result["vol_ratio"] = round(result["est_full_day_100m"] / result["avg5_100m"], 2)
            result["vol_diff_100m"] = round(result["est_full_day_100m"] - result["avg5_100m"], 1)
            result["vol_diff_pct"] = round(
                (result["est_full_day_100m"] - result["avg5_100m"]) / result["avg5_100m"] * 100, 1)
    except Exception as e:
        result["error"] = str(e)

    _volume_cache["data"] = result
    _volume_cache["ts"] = now
    return result


def _price_volume_label(change_pct, vol_ratio):
    """經典量價四象限判斷：價漲量增/價漲量縮/價跌量增/價跌量縮。"""
    if change_pct is None or vol_ratio is None:
        return None
    price_dir = "up" if change_pct > 0.05 else ("down" if change_pct < -0.05 else "flat")
    vol_dir = "up" if vol_ratio >= 1.1 else ("down" if vol_ratio <= 0.9 else "flat")
    labels = {
        ("up", "up"): "價漲量增（多方認同，趨勢延續機率較高）",
        ("up", "down"): "價漲量縮（上漲乏力，慎防假突破）",
        ("up", "flat"): "價漲量平",
        ("down", "up"): "價跌量增（賣壓沉重，留意主跌段）",
        ("down", "down"): "價跌量縮（惜售，賣壓降低，留意止跌訊號）",
        ("down", "flat"): "價跌量平",
        ("flat", "up"): "價平量增（方向未明，觀察後續）",
        ("flat", "down"): "價平量縮",
        ("flat", "flat"): "價量平淡",
    }
    return labels.get((price_dir, vol_dir), "量價關係不明")


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

    try:
        result["oil"] = _oil_price()
    except Exception:
        result["oil"] = None

    try:
        today_amount = result["index"].pop("_total_amount", None)
        vol_stats = _fetch_volume_stats(today_amount=today_amount)
        result["index"]["volume"] = vol_stats
        result["index"]["volume_label"] = _price_volume_label(
            result["index"].get("change_pct"), vol_stats.get("vol_ratio"))
    except Exception:
        result["index"]["volume"] = None
        result["index"]["volume_label"] = None

    result["ok"] = True
    result["updated"] = time.strftime("%H:%M:%S")
    _live_cache["data"] = result
    _live_cache["ts"] = now
    return result


def _fetch_intraday(force=False):
    now = time.time()
    if not force and _intraday_cache["data"] is not None and now - _intraday_cache["ts"] < INTRADAY_CACHE_TTL:
        return _intraday_cache["data"]

    import urllib.request
    try:
        with urllib.request.urlopen(f"{SHIOAJI_GATEWAY}/intraday?code=IX0001", timeout=15) as r:
            result = json.loads(r.read())
    except Exception as e:
        result = {"ok": False, "error": str(e)}

    _intraday_cache["data"] = result
    _intraday_cache["ts"] = now
    return result


def _fetch_stocks_intraday(force=False):
    """六大權值股當日 1 分鐘走勢（走 Shioaji gateway，跟大盤 /intraday 同一支端點）。"""
    now = time.time()
    if not force and _stocks_intraday_cache["data"] is not None and now - _stocks_intraday_cache["ts"] < INTRADAY_CACHE_TTL:
        return _stocks_intraday_cache["data"]

    import urllib.request
    out = []
    for s in STOCKS:
        try:
            with urllib.request.urlopen(f"{SHIOAJI_GATEWAY}/intraday?code={s['code']}", timeout=15) as r:
                resp = json.loads(r.read())
            points = resp.get("points", []) if resp.get("ok") else []
        except Exception:
            points = []
        out.append({"code": s["code"], "name": s["name"], "points": points})

    result = {"ok": True, "stocks": out}
    _stocks_intraday_cache["data"] = result
    _stocks_intraday_cache["ts"] = now
    return result


def _fetch_regional_intraday(force=False):
    """日經225、韓股KOSPI 當日 1 分鐘走勢（yfinance，Shioaji 無海外指數）。"""
    now = time.time()
    if not force and _regional_intraday_cache["data"] is not None and now - _regional_intraday_cache["ts"] < INTRADAY_CACHE_TTL:
        return _regional_intraday_cache["data"]

    import yfinance as yf
    out = []
    for code, name, ticker in [("N225", "日經225", "^N225"), ("KOSPI", "韓股KOSPI", "^KS11")]:
        try:
            t = yf.Ticker(ticker)
            intraday = t.history(period="1d", interval="1m")["Close"].dropna()
            points = [{"t": idx.strftime("%H:%M"), "price": float(v)} for idx, v in intraday.items()]
            daily = t.history(period="5d")["Close"].dropna()
            prev_close = float(daily.iloc[-2]) if len(daily) >= 2 else None
            out.append({"code": code, "name": name, "points": points, "prev_close": prev_close})
        except Exception:
            out.append({"code": code, "name": name, "points": [], "prev_close": None})

    result = {"ok": True, "regional": out}
    _regional_intraday_cache["data"] = result
    _regional_intraday_cache["ts"] = now
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


@app.get("/api/intraday")
def intraday_api():
    try:
        return jsonify(_fetch_intraday(force=request.args.get("fresh") == "1"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/stocks_intraday")
def stocks_intraday_api():
    try:
        return jsonify(_fetch_stocks_intraday(force=request.args.get("fresh") == "1"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/regional_intraday")
def regional_intraday_api():
    try:
        return jsonify(_fetch_regional_intraday(force=request.args.get("fresh") == "1"))
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
