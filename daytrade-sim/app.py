"""當沖模擬（即時報價版，port 5460）

透過 shioaji-gateway (port 5455) 取得即時報價，不自己開 Shioaji 連線。
即時買賣/部位狀態存在瀏覽器 localStorage（換代號、重置都只動這份）；
每一筆成交跟每日損益彙總另外寫進 daytrade.db（SQLite），永久保存、
不受瀏覽器資料被清掉或「重置」按鈕影響。
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daytrade.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            qty INTEGER NOT NULL,
            fee REAL NOT NULL DEFAULT 0,
            tax REAL NOT NULL DEFAULT 0,
            realized REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


_init_db()

SHIOAJI_GATEWAY = "http://127.0.0.1:5455"
TWSE_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
QUOTE_CACHE_TTL = 1
INTRADAY_CACHE_TTL = 30
MOVERS_CACHE_TTL = 20
BIDASK_CACHE_TTL = 1
NAME_CACHE_TTL = 86400

VOL_RANK_CACHE_FILE = "/tmp/intraday_vol_rank_cache.json"
VOLUME_THRESHOLD = 15000  # 張
CHANGE_RATE_THRESHOLD = 3  # %

_quote_cache = {}
_intraday_cache = {}
_movers_cache = {"ts": 0, "data": None}
_bidask_cache = {}
_name_cache = {"ts": 0, "map": {}}
_gates_cache = {}
GATES_CACHE_TTL = 3600  # 三關價依前一交易日高低算出，一小時內不會變，快取久一點省 gateway 查詢
_ma_cache = {}
MA_CACHE_TTL = 3600  # 日均線也是用前一交易日以前的收盤算，一小時內不會變
_daily_ohlcv_cache = {}
DAILY_OHLCV_CACHE_TTL = 60  # 日K圖用，含當天盤中資料，不能快取太久


def _get_name(code):
    """代號 → 股名。上市股票表每天快取一次，查不到就回代號本身。"""
    if time.time() - _name_cache["ts"] > NAME_CACHE_TTL:
        try:
            req = urllib.request.Request(TWSE_ALL_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read())
            _name_cache["map"] = {r["Code"]: r["Name"] for r in rows if r.get("Code")}
            _name_cache["ts"] = time.time()
        except Exception:
            pass
    return _name_cache["map"].get(code, code)


def _fetch_gateway(path):
    with urllib.request.urlopen(f"{SHIOAJI_GATEWAY}{path}", timeout=15) as resp:
        return json.loads(resp.read())


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    try:
        gw = _fetch_gateway("/health")
        return jsonify({"ok": True, "gateway": gw})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/quote")
def quote():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cached = _quote_cache.get(code)
    if cached and time.time() - cached["ts"] < QUOTE_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        gw = _fetch_gateway(f"/snapshot?codes={urllib.parse.quote(code)}")
        if not gw.get("ok") or code not in gw.get("data", {}):
            data = {"ok": False, "error": "查無報價，確認代號或非交易時間"}
        else:
            d = gw["data"][code]
            data = {
                "ok": True,
                "code": code,
                "name": _get_name(code),
                "price": d["close"],
                "change_price": d["change_price"],
                "change_rate": d["change_rate"],
                "ts": time.time(),
            }
        _quote_cache[code] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/stock_search")
def stock_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": True, "results": []})
    try:
        gw = _fetch_gateway(f"/stock_search?q={urllib.parse.quote(q)}")
        return jsonify(gw)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/intraday")
def intraday():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cached = _intraday_cache.get(code)
    if cached and time.time() - cached["ts"] < INTRADAY_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        gw = _fetch_gateway(f"/intraday?code={urllib.parse.quote(code)}")
        data = gw if gw.get("ok") else {"ok": False, "error": "查無日內走勢"}
        _intraday_cache[code] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/trade_log")
def log_trade():
    """把一筆成交永久寫進 SQLite，跟瀏覽器 localStorage 的即時狀態分開，重置/清瀏覽器資料都不會動到。"""
    d = request.get_json(silent=True) or {}
    required = ["date", "time", "code", "action", "price", "qty"]
    if not all(k in d for k in required):
        return jsonify({"ok": False, "error": "缺少必要欄位"}), 400
    try:
        conn = _get_db()
        cur = conn.execute(
            "INSERT INTO trades (date, time, code, name, action, price, qty, fee, tax, realized, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (d["date"], d["time"], d["code"], d.get("name") or d["code"], d["action"],
             d["price"], d["qty"], d.get("fee", 0), d.get("tax", 0), d.get("realized", 0), time.time()),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return jsonify({"ok": True, "id": row_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.delete("/api/trade_log/<int:trade_id>")
def delete_trade_log(trade_id: int):
    """刪除前端交易紀錄時，一併把後端 SQLite 對應那筆刪掉，每日損益紀錄才不會跟畫面對不起來。"""
    try:
        conn = _get_db()
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/daily_pnl")
def daily_pnl():
    """依日期彙總已實現損益，realized 已經是扣完手續費/證交稅的淨額（見前端 applyTradeFor）。"""
    try:
        conn = _get_db()
        rows = conn.execute("""
            SELECT date,
                   SUM(realized) AS realized_pnl,
                   SUM(fee) AS fee_total,
                   SUM(tax) AS tax_total,
                   COUNT(*) AS trade_count
            FROM trades
            GROUP BY date
            ORDER BY date DESC
        """).fetchall()
        conn.close()
        return jsonify({"ok": True, "days": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/three_gates")
def three_gates():
    """三關價：用前一交易日高低算出上關/中關/下關，公式 MID=(H+L)/2，UP=H+(H-L)*0.382，DN=L-(H-L)*0.382。"""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cached = _gates_cache.get(code)
    if cached and time.time() - cached["ts"] < GATES_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        gw = _fetch_gateway(f"/daily_ohlcv?code={urllib.parse.quote(code)}&days=5")
        bars = gw.get("bars", []) if gw.get("ok") else []
        if len(bars) < 2:
            data = {"ok": False, "error": "無足夠日K資料"}
        else:
            prev = bars[-2]  # 最後一筆可能是今天（盤中），前一筆才是完整的前一交易日
            high, low = prev["high"], prev["low"]
            rng = high - low
            data = {
                "ok": True,
                "date": prev["date"],
                "up": round(high + rng * 0.382, 2),
                "mid": round((high + low) / 2, 2),
                "dn": round(low - rng * 0.382, 2),
                "prev_close": prev["close"],
            }
        _gates_cache[code] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/daily_ma")
def daily_ma():
    """5日/10日/月線(20日)均線，用前一交易日以前的日收盤算，不含今天盤中這筆。"""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cached = _ma_cache.get(code)
    if cached and time.time() - cached["ts"] < MA_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        gw = _fetch_gateway(f"/daily_ohlcv?code={urllib.parse.quote(code)}&days=40")
        bars = gw.get("bars", []) if gw.get("ok") else []
        closes = [b["close"] for b in bars[:-1]]  # 最後一筆可能是今天（盤中），排除掉
        if len(closes) < 5:
            data = {"ok": False, "error": "無足夠日K資料"}
        else:
            def ma(period):
                return round(sum(closes[-period:]) / period, 2) if len(closes) >= period else None
            data = {"ok": True, "ma5": ma(5), "ma10": ma(10), "ma20": ma(20)}
        _ma_cache[code] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/daily_ohlcv")
def daily_ohlcv():
    """日K蠟燭圖用的開高低收量，直接轉發 shioaji-gateway 已經聚合好的資料。"""
    code = request.args.get("code", "").strip()
    days = request.args.get("days", "60")
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cache_key = f"{code}:{days}"
    cached = _daily_ohlcv_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < DAILY_OHLCV_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        gw = _fetch_gateway(f"/daily_ohlcv?code={urllib.parse.quote(code)}&days={urllib.parse.quote(days)}")
        data = gw if gw.get("ok") else {"ok": False, "error": "查無日K資料"}
        _daily_ohlcv_cache[cache_key] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/bidask")
def bidask():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    cached = _bidask_cache.get(code)
    if cached and time.time() - cached["ts"] < BIDASK_CACHE_TTL:
        return jsonify(cached["data"])

    try:
        data = _fetch_gateway(f"/bidask?code={urllib.parse.quote(code)}")
        _bidask_cache[code] = {"ts": time.time(), "data": data}
        return jsonify(data)
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/movers")
def movers():
    """今日成交量>15,000張且漲跌幅>=3%的個股，資料源＝finmind vol_rank_updater 快取(成交量前50大) + shioaji-gateway 即時報價。"""
    fresh = request.args.get("fresh") == "1"
    if not fresh and _movers_cache["data"] and time.time() - _movers_cache["ts"] < MOVERS_CACHE_TTL:
        return jsonify(_movers_cache["data"])

    try:
        with open(VOL_RANK_CACHE_FILE, encoding="utf-8") as f:
            vol_rank = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"ok": False, "error": "成交量排行快取不存在或已損毀（vol_rank_updater 只在交易時間9-13點每30分執行一次）"}
        return jsonify(data)

    candidates = {
        code: det for code, det in vol_rank.get("details", {}).items()
        if det.get("total_vol", 0) > VOLUME_THRESHOLD
    }
    if not candidates:
        data = {"ok": True, "updated_at": vol_rank.get("updated_at"), "gainers": [], "losers": []}
        _movers_cache["ts"] = time.time()
        _movers_cache["data"] = data
        return jsonify(data)

    try:
        gw = _fetch_gateway(f"/snapshot?codes={urllib.parse.quote(','.join(candidates))}")
    except (urllib.error.URLError, TimeoutError) as e:
        return jsonify({"ok": False, "error": f"無法連線 shioaji-gateway：{e}"})

    if not gw.get("ok"):
        return jsonify({"ok": False, "error": "shioaji-gateway 回應失敗"})

    gainers, losers = [], []
    for code, snap in gw.get("data", {}).items():
        rate = snap.get("change_rate")
        if rate is None:
            continue
        row = {
            "code": code,
            "name": candidates[code]["name"],
            "price": snap["close"],
            "change_price": snap.get("change_price"),
            "change_rate": rate,
            "volume": candidates[code]["total_vol"],
        }
        if rate >= CHANGE_RATE_THRESHOLD:
            gainers.append(row)
        elif rate <= -CHANGE_RATE_THRESHOLD:
            losers.append(row)

    gainers.sort(key=lambda r: -r["change_rate"])
    losers.sort(key=lambda r: r["change_rate"])

    data = {"ok": True, "updated_at": vol_rank.get("updated_at"), "gainers": gainers, "losers": losers}
    _movers_cache["ts"] = time.time()
    _movers_cache["data"] = data
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5460, debug=False)
