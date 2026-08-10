# -*- coding: utf-8 -*-
"""chip-tracker Web（port 5850）：讀 SQLite 最新資料渲染，不在請求中抓外部資料。"""

import re
import subprocess
import sys
import os

from flask import Flask, jsonify, render_template, request

import db
import updater

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
_CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")


def _stock_payload(conn, s: dict) -> dict:
    code = s["code"]
    daily = db.get_daily_history(conn, code, days=30)   # date DESC
    weekly = db.get_weekly_history(conn, code, weeks=13)  # date DESC

    def _latest(key):
        """各指標公布時間不同（盤中價先出、法人/融資收盤後），各自取最新有值列。"""
        for d in daily:
            if d.get(key) is not None:
                return d
        return {}

    price_row = _latest("close")
    inst_row = _latest("total_net")
    # 連買天數只看已公布法人的交易日，略過今日尚未公布的空列
    inst_days = [d for d in daily if d.get("total_net") is not None]
    foreign_series = [d["foreign_net"] for d in inst_days]
    trust_series = [d["trust_net"] for d in inst_days]

    margin_rows = [d for d in daily if d.get("margin_balance") is not None]
    margin_change = None
    if len(margin_rows) >= 2:
        margin_change = margin_rows[0]["margin_balance"] - margin_rows[1]["margin_balance"]

    # 外資持股比率：取最近兩個有值的日子算日增減
    fr = [(d["date"], d["foreign_ratio"]) for d in daily if d.get("foreign_ratio") is not None]
    foreign_ratio = fr[0][1] if fr else None
    foreign_ratio_date = fr[0][0] if fr else None
    foreign_ratio_change = round(fr[0][1] - fr[1][1], 2) if len(fr) >= 2 else None
    foreign_ratio_prev_date = fr[1][0] if len(fr) >= 2 else None

    w_latest = weekly[0] if weekly else {}
    w_prev = weekly[1] if len(weekly) >= 2 else {}
    whale_change = None
    if w_latest.get("whale_holders") is not None and w_prev.get("whale_holders") is not None:
        whale_change = w_latest["whale_holders"] - w_prev["whale_holders"]

    return {
        "code": code,
        "name": s["name"],
        "date": price_row.get("date"),
        "close": price_row.get("close"),
        "change_pct": price_row.get("change_pct"),
        "inst_date": inst_row.get("date"),
        "foreign_net": inst_row.get("foreign_net"),
        "trust_net": inst_row.get("trust_net"),
        "dealer_net": inst_row.get("dealer_net"),
        "total_net": inst_row.get("total_net"),
        "foreign_streak": db.streak(foreign_series),
        "trust_streak": db.streak(trust_series),
        "margin_balance": margin_rows[0]["margin_balance"] if margin_rows else None,
        "margin_change": margin_change,
        "foreign_ratio": foreign_ratio,
        "foreign_ratio_date": foreign_ratio_date,
        "foreign_ratio_change": foreign_ratio_change,
        "foreign_ratio_prev_date": foreign_ratio_prev_date,
        "big400_pct": w_latest.get("big400_pct"),
        "big400_prev": w_prev.get("big400_pct"),
        "whale_holders": w_latest.get("whale_holders"),
        "whale_change": whale_change,
        "retail_pct": w_latest.get("retail_pct"),
        "retail_prev": w_prev.get("retail_pct"),
        "tdcc_date": w_latest.get("date"),
        "tdcc_prev_date": w_prev.get("date"),
        "big400_series": [
            {"date": w["date"], "pct": w["big400_pct"]} for w in reversed(weekly)
        ],
        "margin_series": [
            {"date": d["date"], "v": d["margin_balance"]}
            for d in reversed(daily) if d.get("margin_balance") is not None
        ],
        "foreign_ratio_series": [
            {"date": d["date"], "v": d["foreign_ratio"]}
            for d in reversed(daily) if d.get("foreign_ratio") is not None
        ],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/data")
def api_data():
    conn = db.get_conn()
    try:
        payload = {"holding": [], "watch": [], "last_update": db.get_meta(conn, "last_update")}
        for s in db.list_stocks(conn):
            payload[s["list_type"]].append(_stock_payload(conn, s))
        return jsonify(payload)
    finally:
        conn.close()


@app.post("/api/stocks")
def api_add_stock():
    data = request.get_json(force=True)
    query = str(data.get("code", "")).strip()
    list_type = data.get("list_type", "watch")
    if not query or list_type not in ("holding", "watch"):
        return jsonify({"ok": False, "error": "請輸入代碼或股名"}), 400
    try:
        code, name = updater.resolve_query(query)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"查詢失敗：{e}"}), 502
    if not code:
        return jsonify({"ok": False, "error": f"查無「{query}」"}), 404
    if not _CODE_RE.match(code):
        return jsonify({"ok": False, "error": f"不支援的代碼格式 {code}"}), 400

    conn = db.get_conn()
    try:
        db.add_stock(conn, code, name, list_type)
        updater.backfill(conn, code)  # 同步回補 45 天，約 3-5 秒
        try:
            updater.backfill_tdcc(conn, code)  # 近 12 週股權分散，約 10-20 秒
        except Exception as e:
            app.logger.warning("backfill_tdcc %s 失敗（週資料等排程補當週）: %s", code, e)
        try:
            updater.backfill_foreign(conn, [code], days=5)  # 外資持股近 5 天，日增減立即可算
        except Exception as e:
            app.logger.warning("backfill_foreign %s 失敗: %s", code, e)
        return jsonify({"ok": True, "code": code, "name": name})
    finally:
        conn.close()


@app.post("/api/stocks/remove")
def api_remove_stock():
    data = request.get_json(force=True)
    code = str(data.get("code", "")).strip().upper()
    conn = db.get_conn()
    try:
        db.remove_stock(conn, code)
        return jsonify({"ok": True})
    finally:
        conn.close()


_sj_api = None


def _get_shioaji():
    """Shioaji 即時行情 singleton；憑證讀 .secrets/shioaji.env。"""
    global _sj_api
    if _sj_api is None:
        import shioaji as sj

        creds = {}
        with open(os.path.expanduser("~/CCProject/.secrets/shioaji.env")) as f:
            for line in f:
                key, _, val = line.strip().partition("=")
                if key:
                    creds[key] = val
        api = sj.Shioaji(simulation=False)
        api.login(api_key=creds["SHIOAJI_API_KEY"], secret_key=creds["SHIOAJI_SECRET_KEY"],
                  contracts_timeout=15000)
        _sj_api = api
    return _sj_api


_quote_cache = {"ts": 0.0, "data": {}}
_QUOTE_TTL = 60


@app.get("/api/quotes")
def api_quotes():
    """全清單即時報價：Shioaji snapshot 優先，配額耗盡/失敗退 yfinance（延遲），60 秒快取。

    fail-open：全部失敗回空 dict，前端保留 DB 收盤價。
    """
    import time as _time

    now = _time.time()
    if now - _quote_cache["ts"] < _QUOTE_TTL and _quote_cache["data"]:
        return jsonify(_quote_cache["data"])

    conn = db.get_conn()
    try:
        codes = [s["code"] for s in db.list_stocks(conn)]
    finally:
        conn.close()

    global _sj_api
    result = {}
    # Shioaji session token 約 24h 過期（401 Token is expired）→ 重置 singleton 重登一次
    for attempt in (1, 2):
        try:
            api = _get_shioaji()
            contracts = []
            for c in codes:
                ct = api.Contracts.Stocks.TSE.get(c) or api.Contracts.Stocks.OTC.get(c)
                if ct is not None:
                    contracts.append(ct)
            for snap in api.snapshots(contracts):
                price = float(snap.close)
                prev = price - float(snap.change_price)
                result[snap.code] = {
                    "price": price,
                    "change_pct": round(float(snap.change_price) / prev * 100, 2) if prev else None,
                    # Shioaji snap.ts 內部以台灣本地時間編碼、未做 UTC 轉換，換算後會超前真實 UTC 8 小時，需扣回
                    "ts": int(snap.ts // 1_000_000_000) - 8 * 3600,
                    "delayed": False,
                }
            break
        except Exception as e:
            app.logger.warning("Shioaji 即時報價失敗(第%d次): %s", attempt, e)
            _sj_api = None

    # Shioaji 每日流量配額（500MB）耗盡時 snapshots 回空 → yfinance 延遲報價備援
    missing = [c for c in codes if c not in result]
    if missing:
        try:
            import yfinance as yf

            for c in missing:
                for suffix in (".TW", ".TWO"):
                    try:
                        fi = yf.Ticker(f"{c}{suffix}").fast_info
                        price = fi.last_price
                        if not price:
                            continue
                        prev = fi.previous_close or 0
                        result[c] = {
                            "price": round(float(price), 2),
                            "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
                            "ts": int(now),
                            "delayed": True,
                        }
                        break
                    except Exception:
                        continue
        except Exception as e:
            app.logger.warning("yfinance 備援失敗: %s", e)

    if result:
        _quote_cache["ts"] = now
        _quote_cache["data"] = result
    return jsonify(result)


@app.post("/api/refresh")
def api_refresh():
    subprocess.Popen([PYTHON, os.path.join(BASE_DIR, "updater.py")],
                     cwd=BASE_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5850)
