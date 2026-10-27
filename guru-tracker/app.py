# -*- coding: utf-8 -*-
"""guru-tracker Web（port 5910）：讀 SQLite 最新資料渲染，不在請求中抓外部資料。"""

import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta

import db
from flask import Flask, jsonify, render_template

import config

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

# 手動更新的執行狀態（只記在這一個常駐行程的記憶體裡；
# 重啟服務就清空，這是刻意的——重啟後本來就沒有「進行中」的更新）
_refresh = {"proc": None, "started_at": None, "finished_at": None, "exit_code": None}

# Flask 預設 threaded=True，兩個分頁同時按「手動更新」會同時通過「沒在跑」的檢查，
# 各自啟動一個 updater（還互相截斷 log）。用鎖把「檢查＋啟動」變成不可分割。
_refresh_lock = threading.Lock()

# 13F 法定申報期限：季末後 45 天（實測多數申報者都在期限當天才送出）
_13F_DEADLINE_DAYS = 45


def _quarter_end(year: int, quarter: int) -> date:
    """回傳該季的最後一天。"""
    return date(
        year,
        quarter * 3,
        [31, 30, 30, 31][quarter - 1],
    )


def _next_13f_deadline(today: date | None = None) -> dict:
    """算出「下一個還沒到的 13F 申報期限」。

    13F 在季末後 45 天內申報。起算點是「剛結束的那一季」，不是當前所在的季
    ——例如 10/27 還在 Q4 進行中，但 Q3（9/30 結束，11/14 截止）尚未到期，
    要回報的是 Q3 而不是 Q4。
    """
    today = today or date.today()
    # 以絕對月份計算避免跨年錯誤（0-based）
    abs_month = today.year * 12 + (today.month - 1)
    for step in range(-1, 6):
        y, mo = divmod(abs_month + step * 3, 12)
        q = mo // 3 + 1
        deadline = _quarter_end(y, q) + timedelta(days=_13F_DEADLINE_DAYS)
        if deadline >= today:
            return {
                "quarter": f"{y}-Q{q}",
                "deadline": deadline.isoformat(),
                "days_left": (deadline - today).days,
            }
    return {}



def _holder_summary(conn, holder: dict) -> dict:
    periods = db.latest_periods(conn, holder["id"], limit=1)
    latest_period = periods[0] if periods else None
    snapshot = db.get_snapshot(conn, holder["id"], latest_period) if latest_period else []
    top3 = snapshot[:3]
    total_value_usd = sum(r["value_usd"] for r in snapshot if r["value_usd"]) or None

    fullness_pct = None
    if holder["type"] == "ark" and total_value_usd:
        all_periods = db.latest_periods(conn, holder["id"], limit=365)
        peak = 0.0
        for p in all_periods:
            snap = db.get_snapshot(conn, holder["id"], p)
            total = sum(r["value_usd"] for r in snap if r["value_usd"]) or 0.0
            if total > peak:
                peak = total
        if peak:
            fullness_pct = round(total_value_usd / peak * 100, 1)

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
        "fullness_pct": fullness_pct,
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


def _position_history(conn, since_period: str = "2025-Q1") -> dict:
    """每位大老「總持股市值」隨季度變化，以自己歷史最高值為滿倉基準(100%)正規化，
    用來看誰現在是重倉／誰在減碼。木頭姐(ARK)資料是逐日更新非季度，不納入此季線比較。"""
    lines = []
    all_periods: set = set()
    for h in config.HOLDERS:
        if h["type"] != "13f":
            continue
        hid = h["id"]
        periods = sorted(db.latest_periods(conn, hid, limit=50))
        series = []
        for p in periods:
            snap = db.get_snapshot(conn, hid, p)
            total = sum(r["value_usd"] for r in snap if r["value_usd"]) or None
            if total:
                series.append((p, total))
        if not series:
            continue
        peak = max(v for _, v in series)
        shown = [(p, v) for p, v in series if p >= since_period]
        if not shown:
            continue
        all_periods.update(p for p, _ in shown)
        lines.append({
            "id": hid, "name_zh": h["name_zh"],
            "points": [{"period": p, "pct_of_peak": round(v / peak * 100, 1), "value_usd": v} for p, v in shown],
            "single_point": len(series) < 2,
        })
    # ARK 每日資料：取樣 ~25 點供卡片 sparkline
    ark_line = None
    for h in config.HOLDERS:
        if h["type"] != "ark":
            continue
        hid = h["id"]
        daily_periods = sorted(db.latest_periods(conn, hid, limit=365))
        series = []
        for p in daily_periods:
            snap = db.get_snapshot(conn, hid, p)
            total = sum(r["value_usd"] for r in snap if r["value_usd"]) or None
            if total:
                series.append((p, total))
        if not series:
            continue
        peak = max(v for _, v in series)
        step = max(1, len(series) // 25)
        sampled = series[::step]
        if sampled[-1] != series[-1]:
            sampled.append(series[-1])
        ark_line = {
            "id": hid, "name_zh": h["name_zh"],
            "points": [{"date": p, "pct_of_peak": round(v / peak * 100, 1), "value_usd": v}
                       for p, v in sampled],
        }
        break  # 只有一個 ARK holder

    return {"ok": True, "periods": sorted(all_periods), "lines": lines, "ark_line": ark_line}


@app.get("/api/position-history")
def api_position_history():
    conn = db.get_conn()
    try:
        return jsonify(_position_history(conn))
    finally:
        conn.close()


def _steven_zhou_entries(conn) -> dict:
    """Steven周交集持股：每位大老「首見於我方追蹤資料庫」的季度，
    以及當季申報市值÷股數換算的隱含均價（13F 不揭露實際成交價，這是推算值）。"""
    real_holders = {h["id"]: h["name_zh"] for h in config.HOLDERS}

    period = db.latest_periods(conn, config.STEVEN_ZHOU_ID, limit=1)
    sz_rows = db.get_snapshot(conn, config.STEVEN_ZHOU_ID, period[0]) if period else []
    sz_rows = sorted(sz_rows, key=lambda r: -(r["weight_pct"] or 0))

    blocks = []
    for sr in sz_rows:
        ticker = sr["ticker"]
        entries = []
        for hid in real_holders:
            periods = sorted(db.latest_periods(conn, hid, limit=50))
            if not periods:
                continue
            history_start = periods[0]
            first = None
            for p in periods:
                row = next((r for r in db.get_snapshot(conn, hid, p) if r["ticker"] == ticker), None)
                if row:
                    first = (p, row["shares"], row["value_usd"])
                    break
            if first:
                p, shares, value = first
                implied_price = (value / shares) if (shares and value) else None
                entries.append({
                    "holder": hid, "holder_zh": real_holders[hid], "first_period": p,
                    "shares": shares, "value_usd": value, "implied_price": implied_price,
                    "uncertain": p == history_start,
                })
        entries.sort(key=lambda e: e["first_period"])
        blocks.append({"ticker": ticker, "weight_pct": round(sr["weight_pct"] or 0, 2), "entries": entries})
    return {"ok": True, "blocks": blocks}


@app.get("/api/holder/steven_zhou/entries")
def api_steven_zhou_entries():
    conn = db.get_conn()
    try:
        return jsonify(_steven_zhou_entries(conn))
    finally:
        conn.close()


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


@app.get("/api/data-status")
def api_data_status():
    """資料新鮮度：最新季度、各來源最新期間、下一個 13F 申報期限。

    用途：說明「為什麼最新一季還沒出現」——13F 有 45 天法定申報期，
    期限前所有申報者都還沒送件是正常的，不是程式沒抓到。
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(period) FROM holdings_snapshot WHERE period LIKE '____-Q_'"
        ).fetchone()
        latest_quarter = row[0] if row and row[0] else None

        row = conn.execute(
            "SELECT MAX(period) FROM holdings_snapshot WHERE period LIKE '____-__-__'"
        ).fetchone()
        latest_daily = row[0] if row and row[0] else None

        per_holder = conn.execute(
            "SELECT h.name_zh, MAX(s.period) AS p "
            "FROM holders h LEFT JOIN holdings_snapshot s ON s.holder_id = h.id "
            "WHERE h.type = '13f' GROUP BY h.id ORDER BY p DESC, h.name_zh"
        ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "latest_quarter": latest_quarter,
        "latest_daily": latest_daily,
        "next_deadline": _next_13f_deadline(),
        "holders": [{"name": r["name_zh"], "period": r["p"]} for r in per_holder],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    })


@app.post("/api/refresh")
def api_refresh():
    """觸發 updater.py。已在執行中則回 409，不重複啟動。"""
    with _refresh_lock:
        proc = _refresh["proc"]
        if proc is not None and proc.poll() is None:
            return jsonify({
                "status": "running",
                "started_at": _refresh["started_at"],
            }), 409

        log_path = os.path.join(BASE_DIR, "manual_refresh.log")
        try:
            # 'a' 而非 'w'：保留上一次執行的 log，失敗後重試才查得到前一次的錯誤
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== 手動更新 {datetime.now().isoformat(timespec='seconds')} =====\n")
                fh.flush()
                # 先記下「本次輸出從這裡開始」的位置，再啟動，
                # 否則 updater 可能已經寫入前幾行而被漏掉
                offset = fh.tell()
                proc = subprocess.Popen(
                    [PYTHON, os.path.join(BASE_DIR, "updater.py")],
                    cwd=BASE_DIR,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                )
        except OSError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

        _refresh.update(
            proc=proc,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
            exit_code=None,
            log_offset=offset,
        )
    return jsonify({"status": "started", "started_at": _refresh["started_at"]})


@app.get("/api/refresh/status")
def api_refresh_status():
    """查手動更新進度。完成後附上本次執行的 log 尾段。"""
    proc = _refresh["proc"]
    if proc is None:
        return jsonify({"status": "idle"})

    code = proc.poll()
    if code is None:
        return jsonify({"status": "running", "started_at": _refresh["started_at"]})

    if _refresh["finished_at"] is None:
        _refresh["finished_at"] = datetime.now().isoformat(timespec="seconds")
        _refresh["exit_code"] = code

    tail = _read_run_log(_refresh.get("log_offset") or 0)

    return jsonify({
        "status": "done",
        "ok": code == 0,
        "exit_code": code,
        "started_at": _refresh["started_at"],
        "finished_at": _refresh["finished_at"],
        "log_tail": tail,
    })


def _read_run_log(offset: int, max_lines: int = 20) -> list[str]:
    """只讀「本次執行」寫入的段落（offset 之後），避免顯示上一次的殘留。"""
    path = os.path.join(BASE_DIR, "manual_refresh.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            lines = fh.read().strip().splitlines()
        return lines[-max_lines:]
    except OSError:
        return []


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5910)
