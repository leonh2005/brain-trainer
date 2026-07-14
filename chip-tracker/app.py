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

    latest = daily[0] if daily else {}
    foreign_series = [d["foreign_net"] for d in daily]
    trust_series = [d["trust_net"] for d in daily]

    margin_change = None
    if len(daily) >= 2 and daily[0].get("margin_balance") is not None \
            and daily[1].get("margin_balance") is not None:
        margin_change = daily[0]["margin_balance"] - daily[1]["margin_balance"]

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
        "date": latest.get("date"),
        "close": latest.get("close"),
        "change_pct": latest.get("change_pct"),
        "foreign_net": latest.get("foreign_net"),
        "trust_net": latest.get("trust_net"),
        "dealer_net": latest.get("dealer_net"),
        "total_net": latest.get("total_net"),
        "foreign_streak": db.streak(foreign_series),
        "trust_streak": db.streak(trust_series),
        "margin_balance": latest.get("margin_balance"),
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
        "tdcc_date": w_latest.get("date"),
        "tdcc_prev_date": w_prev.get("date"),
        "big400_series": [
            {"date": w["date"], "pct": w["big400_pct"]} for w in reversed(weekly)
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


@app.post("/api/refresh")
def api_refresh():
    subprocess.Popen([PYTHON, os.path.join(BASE_DIR, "updater.py")],
                     cwd=BASE_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5850)
