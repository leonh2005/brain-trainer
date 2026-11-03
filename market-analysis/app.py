"""市場分析單頁儀表板 Flask 服務 (port 5350)

/api/analysis -> analysis_data.json 內容
/api/live     -> 大盤+六檔權值股即時報價 (Shioaji 優先, yfinance fallback, 30秒快取)
"""
import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_JSON = os.path.join(BASE_DIR, "analysis_data.json")
HITS_JSON = os.path.join(BASE_DIR, "hit_counts.json")
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
    with open(path, encoding="utf-8") as f:
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


# 日韓大盤：唯一來源是新浪 znb_* 即時報價（延遲約 10 秒）。
# 2026-10-29 實測：yfinance 免費源延遲 15-21 分鐘；新浪 int_nikkei / int_kospi 是壞別名
# （分別回 44,946 與空字串），只有 znb_NKY / znb_KOSPI 與 yfinance 前收一致可用。
# 2026-11-03 起 yfinance 完全退出區域指數的「報價」角色（仍用在日波圖畫曲線）：
# 它的日線時間戳是 00:00 且會跳過交易日（實測 ^KS11 5d 缺 2026-11-02），當備援會算錯漲跌幅。
REGIONAL_INDICES = (
    ("N225", "日經225", "znb_NKY", "^N225"),
    ("KOSPI", "韓股KOSPI", "znb_KOSPI", "^KS11"),
)
SINA_QUOTE_MAX_AGE = 600  # 報價停超過 10 分鐘只標示 stale，不丟棄：休市/午休/收盤後本來就沒有新 tick
SINA_TZ = timezone(timedelta(hours=8))      # 新浪 znb_* 時間戳為北京時間
EXCHANGE_TZ = timezone(timedelta(hours=9))  # 日/韓交易所；日波圖 X 軸與新鮮度徽章都用這個時區
_NO_FRESHNESS = {"as_of": None, "as_of_time": None, "is_today": None, "is_stale": None}


def _quote_age_seconds(ts: datetime, now: datetime | None = None) -> float:
    """報價時間戳距 now（預設現在）幾秒。naive ts 視為交易所時區。"""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=EXCHANGE_TZ)
    return ((now or datetime.now(UTC)).astimezone(UTC) - ts).total_seconds()


def _freshness(ts: datetime, now: datetime | None = None) -> dict:
    """報價時間戳 → 前端標示用欄位。

    時間一律用交易所時區：圖表 X 軸是 JST/KST，徽章若換成台北時間會在同一區塊
    出現相差一小時的两个時間。is_today 用來區分「今天但停滯」與「非今天」。
    """
    if ts.tzinfo is None:      # 與 _quote_age_seconds 對 naive 的解讀保持一致
        ts = ts.replace(tzinfo=EXCHANGE_TZ)
    now = now or datetime.now(EXCHANGE_TZ)
    local = ts.astimezone(EXCHANGE_TZ)
    return {
        "as_of": local.strftime("%Y-%m-%d"),
        "as_of_time": local.strftime("%H:%M"),
        "is_today": local.date() == now.astimezone(EXCHANGE_TZ).date(),
        "is_stale": _quote_age_seconds(ts, now) > SINA_QUOTE_MAX_AGE,
    }


def _sina_index_quote(symbol):
    """新浪 znb_* 國際指數即時報價，回傳 {price, prev_close, ts} 或 None。
    欄位：名稱,現價,漲跌點,漲跌%,_,_,日期,時間,開,昨收,高,低；時間戳為北京時間 (UTC+8)。"""
    import urllib.request

    req = urllib.request.Request(
        f"https://hq.sinajs.cn/list={symbol}",
        headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        text = resp.read().decode("gbk", errors="replace")
    parts = text.split('"')[1].split(",") if text.count('"') >= 2 else []
    if len(parts) < 10 or not parts[1] or not parts[9]:
        return None
    try:
        price = float(parts[1])
        prev_close = float(parts[9])
        ts = datetime.strptime(f"{parts[6]} {parts[7]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if price <= 0 or prev_close <= 0:
        return None
    if not 0.5 < price / prev_close < 2.0:
        return None                      # 欄位順序若被改動會解析出錯位數字，用漲跌幅合理性擋掉
    ts = ts.replace(tzinfo=SINA_TZ)
    return {"price": price, "prev_close": prev_close, "ts": ts}


def _regional_indices():
    """日股(日經225)、韓股(KOSPI) 大盤，只用新浪 znb_* 即時源。

    刻意不拿 yfinance 當備援：它的日線時間戳是 00:00 且會跳過交易日
    （實測 ^KS11 5d 缺 2026-11-02），prev_close 取 iloc[-2] 會落在別的交易日，
    漲跌幅可差 2~3 個百分點。取不到就讓該指數缺席，比顯示錯誤數字好。
    """
    out = []
    for code, name, sina_symbol, _ticker in REGIONAL_INDICES:
        try:
            quote = _sina_index_quote(sina_symbol)
        except Exception:
            quote = None
        if quote is None:
            continue
        close, prev = quote["price"], quote["prev_close"]
        out.append({
            "code": code,
            "name": name,
            "price": round(close, 2),
            "change_pct": round((close - prev) / prev * 100, 2) if prev else None,
            "change_point": round(close - prev, 2) if prev else None,
            "source": "sina",
            # is_today 讓前端區分「今天但停滯」（顯示截至時間）與「非今天」（顯示資料日期）
            **_freshness(quote["ts"]),
        })
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
    """日經225、韓股KOSPI 當日 1 分鐘走勢（yfinance 畫曲線 + 新浪即時末端點）。"""
    now = time.time()
    if not force and _regional_intraday_cache["data"] is not None and now - _regional_intraday_cache["ts"] < INTRADAY_CACHE_TTL:
        return _regional_intraday_cache["data"]

    import yfinance as yf
    out = []
    for code, name, sina_symbol, ticker in REGIONAL_INDICES:
        try:
            t = yf.Ticker(ticker)
            intraday = t.history(period="1d", interval="1m")["Close"].dropna()
            if intraday.index.tz is None:  # 缺時區資訊時不讓 aware/naive 比較拋 TypeError
                intraday = intraday.tz_localize(EXCHANGE_TZ)
            points = [{"t": idx.strftime("%H:%M"), "price": float(v)} for idx, v in intraday.items()]
            try:
                sina = _sina_index_quote(sina_symbol)
            except Exception:
                sina = None

            # 末端補新浪即時點：yfinance 分K 延遲 15-21 分鐘，不補會跟上方標題的價格對不上。
            # 只有同交易日才補，否則會把別的交易日價格接到今天的曲線末端（跨日假跳空）。
            appended_today = False
            sina_local = None
            if sina and points:
                sina_local = sina["ts"].astimezone(intraday.index[-1].tzinfo)
                if intraday.index[-1] < sina_local and intraday.index[-1].date() == sina_local.date():
                    label = sina_local.strftime("%H:%M")
                    if points[-1]["t"] == label:
                        points[-1]["price"] = round(sina["price"], 2)
                    else:
                        points.append({"t": label, "price": round(sina["price"], 2)})
                    appended_today = True

            # 昨收只信新浪，且必須與曲線同一交易日：跨日時新浪的昨收是「曲線當天的收盤」，畫上去會錯位。
            prev_close = None
            if sina and sina.get("prev_close") and sina_local is not None \
                    and intraday.index[-1].date() == sina_local.date():
                prev_close = sina["prev_close"]

            # 新鮮度以曲線最後一點為準；同日補點時用新浪時間戳，曲線為空時也退回新浪報價
            last_ts = intraday.index[-1].to_pydatetime() if len(intraday) else None
            if appended_today:
                last_ts = sina["ts"]
            elif last_ts is None and sina:
                last_ts = sina["ts"]
            out.append({"code": code, "name": name, "points": points, "prev_close": prev_close,
                        **(_freshness(last_ts) if last_ts else _NO_FRESHNESS)})
        except Exception:
            out.append({"code": code, "name": name, "points": [], "prev_close": None,
                        **_NO_FRESHNESS})

    result = {"ok": True, "regional": out}
    _regional_intraday_cache["data"] = result
    _regional_intraday_cache["ts"] = now
    return result


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/analysis")
def analysis():
    with open(ANALYSIS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    if os.path.exists(HITS_JSON):
        with open(HITS_JSON, encoding="utf-8") as f:
            data["hits"] = json.load(f).get("hits", {})
    else:
        data["hits"] = {}
    return jsonify(data)


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
