# -*- coding: utf-8 -*-
"""guru-tracker Web（port 5910）：讀 SQLite 最新資料渲染，不在請求中抓外部資料。"""

import os
import subprocess
import sys

from flask import Flask, jsonify, render_template

import db

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


def _holder_summary(conn, holder: dict) -> dict:
    periods = db.latest_periods(conn, holder["id"], limit=1)
    latest_period = periods[0] if periods else None
    snapshot = db.get_snapshot(conn, holder["id"], latest_period) if latest_period else []
    top3 = snapshot[:3]
    return {
        "id": holder["id"],
        "name": holder["name"],
        "name_zh": holder["name_zh"],
        "type": holder["type"],
        "source": holder["source"],
        "updated_at": holder["updated_at"],
        "latest_period": latest_period,
        "holding_count": len(snapshot),
        "top3": [{"ticker": r["ticker"], "weight_pct": round(r["weight_pct"] or 0, 2)} for r in top3],
    }


def _holder_detail(conn, holder: dict) -> dict:
    periods = db.latest_periods(conn, holder["id"], limit=2)
    latest = db.get_snapshot(conn, holder["id"], periods[0]) if periods else []
    prev = db.get_snapshot(conn, holder["id"], periods[1]) if len(periods) >= 2 else []
    prev_map = {r["ticker"]: r["weight_pct"] for r in prev}

    sector_totals: dict = {}
    holdings = []
    for r in latest:
        meta = db.get_ticker_sector(conn, r["ticker"]) or {}
        sector = meta.get("sector") or "未分類"
        weight = r["weight_pct"] or 0
        sector_totals[sector] = sector_totals.get(sector, 0) + weight

        prev_weight = prev_map.get(r["ticker"])
        if prev_weight is None:
            change = None
            status = "new"  # 上一期沒有，本期新進
        else:
            change = round(weight - prev_weight, 2)
            status = "changed"
        holdings.append({
            "ticker": r["ticker"],
            "weight_pct": round(weight, 2),
            "value_usd": r["value_usd"],
            "shares": r["shares"],
            "sector": sector,
            "change_pct": change,
            "status": status,
        })

    exited = [t for t in prev_map if t not in {h["ticker"] for h in holdings}]

    return {
        "id": holder["id"],
        "name": holder["name"],
        "name_zh": holder["name_zh"],
        "type": holder["type"],
        "source": holder["source"],
        "cik": holder["cik"],
        "updated_at": holder["updated_at"],
        "latest_period": periods[0] if periods else None,
        "prev_period": periods[1] if len(periods) >= 2 else None,
        "holdings": holdings,
        "exited": exited,
        "sector_breakdown": [{"sector": s, "weight_pct": round(w, 2)} for s, w in
                             sorted(sector_totals.items(), key=lambda x: -x[1])],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/holder/<holder_id>")
def holder_page(holder_id):
    return render_template("holder.html", holder_id=holder_id)


@app.get("/api/holders")
def api_holders():
    conn = db.get_conn()
    try:
        holders = [h for h in db.list_holders(conn) if h["type"] != "virtual"]
        virtual = [h for h in db.list_holders(conn) if h["type"] == "virtual"]
        return jsonify({
            "holders": [_holder_summary(conn, h) for h in holders],
            "steven_zhou": [_holder_summary(conn, h) for h in virtual],
        })
    finally:
        conn.close()


@app.get("/api/holder/<holder_id>")
def api_holder_detail(holder_id):
    conn = db.get_conn()
    try:
        holder = db.get_holder(conn, holder_id)
        if not holder:
            return jsonify({"ok": False, "error": "查無此人物"}), 404
        return jsonify(_holder_detail(conn, holder))
    finally:
        conn.close()


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})


@app.post("/api/refresh")
def api_refresh():
    subprocess.Popen([PYTHON, os.path.join(BASE_DIR, "updater.py")],
                      cwd=BASE_DIR,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5910)
