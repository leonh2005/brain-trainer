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

_quote_cache = {}
_intraday_cache = {}


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5460, debug=False)
