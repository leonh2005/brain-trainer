# -*- coding: utf-8 -*-
"""chip-tracker Web（port 5850）：讀 SQLite 最新資料渲染，不在請求中抓外部資料。"""

import re
import subprocess
import sys
import time
import os
from datetime import date

import requests
from flask import Flask, jsonify, render_template, request

import db
import patterns
import support as support_mod
import updater

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
_CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")

_disposition_cache = {"at": 0, "data": {}}
_DISPOSITION_TTL = 600  # 秒；上游 command-center 本身也快取 30 分鐘


def _current_dispositions() -> dict:
    """目前處置中股票：{code: {"reason": str, "start": str, "end": str}}。抓不到就回空字典（fail-open，不擋頁面）。"""
    now = time.time()
    if now - _disposition_cache["at"] < _DISPOSITION_TTL:
        return _disposition_cache["data"]
    data = {}
    try:
        r = requests.get("http://localhost:5950/api/signals/disposition", timeout=5)
        r.raise_for_status()
        recent = r.json().get("data", {}).get("recent", [])
        today = date.today().isoformat()
        for item in recent:
            if item.get("start", "") <= today <= item.get("end", ""):
                data[item["code"]] = {
                    "reason": item.get("reason"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                }
    except Exception:
        return _disposition_cache["data"]  # 抓不到就沿用舊快取，總比整頁掛掉好
    _disposition_cache["at"] = now
    _disposition_cache["data"] = data
    return data


def _stock_payload(conn, s: dict, dispositions: dict) -> dict:
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
    price_rows = [d for d in daily if d.get("change_pct") is not None]
    price_streak = db.price_streak([d["change_pct"] for d in price_rows])
    price_streak_pct = db.price_streak_pct([d["change_pct"] for d in price_rows], price_streak)
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

    # 型態偵測需要足夠的交易日歷史（四海遊龍需 MA60，至少 61 天），獨立查詢不影響上面既有的 30 天顯示邏輯
    pattern_rows = list(reversed(db.get_daily_history(conn, code, days=65)))  # 依日期舊到新
    stock_patterns = patterns.detect_candlestick(pattern_rows)
    stock_patterns += patterns.detect_ma_patterns(
        [d["close"] for d in pattern_rows if d.get("close") is not None]
    )

    w_latest = weekly[0] if weekly else {}
    w_prev = weekly[1] if len(weekly) >= 2 else {}
    whale_change = None
    whale_change_pct = None
    if w_latest.get("whale_holders") is not None and w_prev.get("whale_holders") is not None:
        whale_change = w_latest["whale_holders"] - w_prev["whale_holders"]
        if w_prev["whale_holders"]:
            whale_change_pct = round(whale_change / w_prev["whale_holders"] * 100, 2)

    return {
        "code": code,
        "name": s["name"],
        "date": price_row.get("date"),
        "close": price_row.get("close"),
        "change_pct": price_row.get("change_pct"),
        "change_point": price_row.get("change_point"),
        "volume": price_row.get("volume"),
        "disposition": dispositions.get(code),
        "price_streak": price_streak,
        "price_streak_pct": price_streak_pct,
        "patterns": stock_patterns,
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
        "whale_change_pct": whale_change_pct,
        "retail_pct": w_latest.get("retail_pct"),
        "retail_prev": w_prev.get("retail_pct"),
        "tdcc_date": w_latest.get("date"),
        "tdcc_prev_date": w_prev.get("date"),
        "big400_series": [
            {"date": w["date"], "pct": w["big400_pct"]} for w in reversed(weekly)
        ],
        "retail_series": [
            {"date": w["date"], "pct": w["retail_pct"]} for w in reversed(weekly)
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
        lists = db.list_lists(conn)
        dispositions = _current_dispositions()
        stocks_by_list = {}
        for s in db.list_stocks(conn):
            stocks_by_list.setdefault(s["list_id"], []).append(_stock_payload(conn, s, dispositions))
        list_payloads = [
            {"id": l["id"], "name": l["name"], "stocks": stocks_by_list.get(l["id"], [])}
            for l in lists
        ]
        by_name = {l["name"]: l["stocks"] for l in list_payloads}
        payload = {
            "lists": list_payloads,
            # command-center 的籌碼卡片直接吃 holding/watch，保留相容
            "holding": by_name.get("庫存", []),
            "watch": by_name.get("觀察", []),
            "last_update": db.get_meta(conn, "last_update"),
        }
        return jsonify(payload)
    finally:
        conn.close()


@app.get("/api/lists")
def api_get_lists():
    conn = db.get_conn()
    try:
        return jsonify(db.list_lists(conn))
    finally:
        conn.close()


@app.post("/api/lists")
def api_add_list():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "請輸入清單名稱"}), 400
    conn = db.get_conn()
    try:
        list_id = db.add_list(conn, name)
        return jsonify({"ok": True, "id": list_id, "name": name})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        conn.close()


@app.delete("/api/lists/<int:list_id>")
def api_delete_list(list_id):
    conn = db.get_conn()
    try:
        db.delete_list(conn, list_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        conn.close()


@app.post("/api/stocks")
def api_add_stock():
    data = request.get_json(force=True)
    query = str(data.get("code", "")).strip()
    list_id = data.get("list_id")

    conn = db.get_conn()
    try:
        valid_ids = {l["id"] for l in db.list_lists(conn)}
    finally:
        conn.close()
    if not query or list_id not in valid_ids:
        return jsonify({"ok": False, "error": "請輸入代碼或股名，並選擇清單"}), 400
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
        db.add_stock(conn, code, name, list_id)
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
    """移除股票：庫存刪除時自動改列觀察（保留追蹤），其餘清單移除才是真的移除。"""
    data = request.get_json(force=True)
    code = str(data.get("code", "")).strip().upper()
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT s.name AS name, l.name AS list_name FROM stocks s "
            "JOIN lists l ON l.id = s.list_id WHERE s.code = ?", (code,)
        ).fetchone()
        if row and row["list_name"] == "庫存":
            watch = conn.execute("SELECT id FROM lists WHERE name = '觀察'").fetchone()
            if watch:
                db.add_stock(conn, code, row["name"], watch["id"])
                return jsonify({"ok": True, "moved_to_watch": True})
        db.remove_stock(conn, code)
        return jsonify({"ok": True, "moved_to_watch": False})
    finally:
        conn.close()


SHIOAJI_GATEWAY = "http://127.0.0.1:5455"

_quote_cache = {"ts": 0.0, "data": {}}
_QUOTE_TTL = 60


@app.get("/api/quotes")
def api_quotes():
    """全清單即時報價：shioaji-gateway（單一共用連線）優先，失敗退 yfinance（延遲），60 秒快取。

    不直連 Shioaji：本機所有服務共用 gateway 唯一連線，各自 login 會互踢/觸發登入限流
    （見 project_shioaji_gateway 記憶）。fail-open：全部失敗回空 dict，前端保留 DB 收盤價。
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

    result = {}
    try:
        r = requests.get(f"{SHIOAJI_GATEWAY}/snapshot", params={"codes": ",".join(codes)}, timeout=15)
        r.raise_for_status()
        resp = r.json()
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "gateway error"))
        for code, d in resp.get("data", {}).items():
            close = d.get("close")
            change_price = d.get("change_price")
            if close is None or change_price is None:
                continue
            prev = close - change_price
            result[code] = {
                "price": float(close),
                "change_pct": d["change_rate"] if d.get("change_rate") is not None
                    else (round(change_price / prev * 100, 2) if prev else None),
                "change_point": round(float(change_price), 2),
                "ts": int(now),
                "delayed": False,
            }
    except Exception as e:
        app.logger.warning("shioaji-gateway 即時報價失敗: %s", e)

    # gateway 掛掉/當日配額耗盡時回空 → yfinance 延遲報價備援
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
                            "change_point": round(price - prev, 2) if prev else None,
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


_support_cache: dict = {}
_SUPPORT_TTL = 300  # 秒


@app.get("/api/chip-support/<code>")
def api_support(code):
    """支撐位分析：走 Shioaji gateway 抓250天日K，5分鐘記憶體快取。
    路徑刻意取名 chip-support（不叫 support）：command-center 自己也有 /api/support/{symbol}，
    透過 /svc/5850/ 代理載入本頁時，若同名會被 command-center 自己的路由攔截，
    根本轉不到這裡（2026-09-02 踩過一次）。"""
    import time as _time

    now = _time.time()
    hit = _support_cache.get(code)
    if hit and now - hit[0] < _SUPPORT_TTL:
        levels, bars = hit[1]
        return jsonify({"ok": True, "code": code, "levels": levels, "bars": bars})

    try:
        r = requests.get(f"{SHIOAJI_GATEWAY}/daily_ohlcv", params={"code": code, "days": 250}, timeout=15)
        r.raise_for_status()
        resp = r.json()
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "gateway error"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    bars = resp.get("bars") or []
    today_str = date.today().isoformat()
    if bars and bars[-1].get("date") == today_str:
        bars = bars[:-1]  # 今天還沒收盤，排除避免支撐位跟現價矛盾
    levels = support_mod.analyze_support(bars)
    chart_bars = bars[-90:]
    _support_cache[code] = (now, (levels, chart_bars))
    return jsonify({"ok": True, "code": code, "levels": levels, "bars": chart_bars})


@app.post("/api/refresh")
def api_refresh():
    subprocess.Popen([PYTHON, os.path.join(BASE_DIR, "updater.py")],
                     cwd=BASE_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5850)
