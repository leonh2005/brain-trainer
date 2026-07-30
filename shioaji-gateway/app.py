"""Shioaji 報價 Gateway (port 5455)

獨佔「唯一」一條 Shioaji 持久連線，供多個服務(market-analysis、ma_monitor…)
共用，避免各自 login 互踢 / 觸發登入限流。

端點：
  GET /health                              -> {status, logged_in}
  GET /snapshot?codes=IX0001,2330,2454     -> {ok, data:{code:{close,change_price,change_rate}}}
  GET /kbars?code=2330&days=30             -> {ok, closes:[...]}  (與 api.kbars(...).Close 相同)
  GET /intraday?code=IX0001                -> {ok, points:[{t:"09:01",price:23458.1},...]}  (當日 1 分鐘走勢)
"""
import os
import threading
from datetime import date, datetime, timedelta, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)
SECRETS_DIR = "/Users/steven/CCProject/.secrets"

_conn = {"api": None}
_lock = threading.Lock()


def _read_secret(filename):
    with open(os.path.join(SECRETS_DIR, filename), "r", encoding="utf-8") as f:
        return f.read().strip()


def _login():
    import shioaji as sj
    api_key = os.environ.get("SHIOAJI_API_KEY") or _read_secret("shioaji_api.txt")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY") or _read_secret("shioaji_secret.txt")
    api = sj.Shioaji(simulation=False)
    api.login(api_key=api_key, secret_key=secret_key)
    return api


def _run(fn):
    """在鎖內用持久連線執行 fn(api)；連線失效時自動重登一次再試。"""
    with _lock:
        try:
            if _conn["api"] is None:
                _conn["api"] = _login()
            return fn(_conn["api"])
        except Exception:
            try:
                if _conn["api"] is not None:
                    try:
                        _conn["api"].logout()
                    except Exception:
                        pass
                _conn["api"] = _login()
                return fn(_conn["api"])
            except Exception:
                _conn["api"] = None
                raise


def _resolve_contract(api, code):
    if code.startswith("IX"):          # 指數，如 IX0001（加權指數）
        return api.Contracts.Indexs.TSE[code]
    # 個股：先找上市(TSE)、再上櫃(OTC)、最後全域 bracket
    return (api.Contracts.Stocks.TSE.get(code)
            or api.Contracts.Stocks.OTC.get(code)
            or api.Contracts.Stocks[code])


@app.get("/health")
def health():
    return jsonify({"status": "ok", "logged_in": _conn["api"] is not None})


@app.get("/snapshot")
def snapshot():
    codes = [c for c in (request.args.get("codes", "").split(",")) if c]
    if not codes:
        return jsonify({"ok": False, "error": "no codes"}), 400
    try:
        def work(api):
            contracts = [_resolve_contract(api, c) for c in codes]
            snaps = api.snapshots(contracts)
            out = {}
            for c, sn in zip(codes, snaps):
                out[c] = {
                    "close": float(sn.close),
                    "change_price": float(sn.change_price),
                    "change_rate": float(sn.change_rate) if sn.change_rate is not None else None,
                }
            return out
        return jsonify({"ok": True, "data": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/kbars")
def kbars():
    code = request.args.get("code", "")
    days = int(request.args.get("days", "30"))
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            end = date.today()
            start = end - timedelta(days=max(days * 2, 60))
            kb = api.kbars(
                contract=contract,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
            )
            return [float(c) for c in kb.Close if c]
        return jsonify({"ok": True, "closes": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/intraday")
def intraday():
    code = request.args.get("code", "")
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            today = date.today().strftime("%Y-%m-%d")
            kb = api.kbars(contract=contract, start=today, end=today)
            points = []
            for ts, close in zip(kb.ts, kb.Close):
                if close is None:
                    continue
                # kb.ts 是「台灣本地時間」數值但以 epoch 秒編碼（非真正 UTC），
                # 用 utcfromtimestamp 直接取數值對應的時鐘時間，避免 fromtimestamp 多轉一次時區造成 +8 小時位移
                t = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                points.append({"t": t.strftime("%H:%M"), "price": float(close)})
            return points
        return jsonify({"ok": True, "points": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5455)
