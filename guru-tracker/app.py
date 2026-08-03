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
    total_value_usd = sum(r["value_usd"] for r in snapshot if r["value_usd"]) or None
    return {
        "id": holder["id"],
        "name": holder["name"],
        "name_zh": holder["name_zh"],
        "type": holder["type"],
        "source": holder["source"],
        "updated_at": holder["updated_at"],
        "latest_period": latest_period,
        "holding_count": len(snapshot),
        "total_value_usd": total_value_usd,
        "top3": [{"ticker": r["ticker"], "weight_pct": round(r["weight_pct"] or 0, 2)} for r in top3],
    }


def _holder_detail(conn, holder: dict) -> dict:
    periods = db.latest_periods(conn, holder["id"], limit=2)
    latest = db.get_snapshot(conn, holder["id"], periods[0]) if periods else []
    prev = db.get_snapshot(conn, holder["id"], periods[1]) if len(periods) >= 2 else []
    prev_map = {r["ticker"]: r for r in prev}
    prev_filed_date = prev[0]["filed_date"] if prev else None

    sector_totals: dict = {}
    holdings = []
    for r in latest:
        meta = db.get_ticker_sector(conn, r["ticker"]) or {}
        sector = meta.get("sector") or "未分類"
        weight = r["weight_pct"] or 0
        sector_totals[sector] = sector_totals.get(sector, 0) + weight

        prev_row = prev_map.get(r["ticker"])
        shares = r["shares"]
        shares_change = None
        shares_change_pct = None
        if prev_row is None:
            weight_change = None
            status = "new"  # 上一期沒有，本期新進
        else:
            weight_change = round(weight - (prev_row["weight_pct"] or 0), 2)
            status = "changed"
            prev_shares = prev_row["shares"]
            if shares is not None and prev_shares:
                shares_change = shares - prev_shares
                shares_change_pct = round(shares_change / prev_shares * 100, 2)
        holdings.append({
            "ticker": r["ticker"],
            "weight_pct": round(weight, 2),
            "value_usd": r["value_usd"],
            "shares": shares,
            "shares_change": shares_change,
            "shares_change_pct": shares_change_pct,
            "sector": sector,
            "change_pct": weight_change,
            "status": status,
            "filed_date": r["filed_date"],
        })

    exited = []
    latest_tickers = {h["ticker"] for h in holdings}
    for ticker, prev_row in prev_map.items():
        if ticker in latest_tickers:
            continue
        exited.append({
            "ticker": ticker,
            "shares": prev_row["shares"],
            "weight_pct": round(prev_row["weight_pct"] or 0, 2),
            "filed_date": prev_row["filed_date"],
        })

    total_holding_count = len(holdings)  # db.get_snapshot 已依 weight_pct DESC 排序，直接截前10大即可

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
        "prev_filed_date": prev_filed_date,
        "holdings": holdings[:10],
        "total_holding_count": total_holding_count,
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
        holder_summaries = [_holder_summary(conn, h) for h in holders]
        holder_summaries.sort(key=lambda s: s["total_value_usd"] or 0, reverse=True)  # 總資產由大到小
        return jsonify({
            "holders": holder_summaries,
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


@app.get("/api/holder/<holder_id>/history/<ticker>")
def api_ticker_history(holder_id, ticker):
    conn = db.get_conn()
    try:
        holder = db.get_holder(conn, holder_id)
        if not holder:
            return jsonify({"ok": False, "error": "查無此人物"}), 404
        rows = db.get_ticker_history(conn, holder_id, ticker)
        return jsonify({
            "ok": True,
            "ticker": ticker,
            "history": [{
                "period": r["period"],
                "weight_pct": round(r["weight_pct"] or 0, 2),
                "shares": r["shares"],
                "filed_date": r["filed_date"],
            } for r in rows],
        })
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
