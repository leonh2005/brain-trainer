"""當沖模擬（即時報價版，port 5460）

透過 shioaji-gateway (port 5455) 取得即時報價，不自己開 Shioaji 連線。
買賣/損益/部位狀態全部存在瀏覽器 localStorage，這裡只負責報價代理。
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SHIOAJI_GATEWAY = "http://127.0.0.1:5455"
QUOTE_CACHE_TTL = 1
INTRADAY_CACHE_TTL = 30
MOVERS_CACHE_TTL = 20
BIDASK_CACHE_TTL = 1

VOL_RANK_CACHE_FILE = "/tmp/intraday_vol_rank_cache.json"
VOLUME_THRESHOLD = 15000  # 張
CHANGE_RATE_THRESHOLD = 3  # %

_quote_cache = {}
_intraday_cache = {}
_movers_cache = {"ts": 0, "data": None}
_bidask_cache = {}


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
