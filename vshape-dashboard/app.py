#!/usr/bin/env python3
"""V 型反轉監控面板 — port 5995

只讀寫 intraday_monitor 產生的兩個檔案，不自己碰行情：
  finmind/.local/vshape_state.json   各標的目前階段（盤中每分鐘更新）
  finmind/.local/vshape_watch.json   監控清單（本頁面可增刪）

即時價格另外向 shioaji-gateway 取，取不到就顯示階段與關鍵價位。
"""

import json
import os
import re
import time
import unicodedata
from datetime import date, datetime

import requests
from flask import Flask, jsonify, render_template, request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH_FILE = os.path.join(BASE, "finmind", ".local", "vshape_watch.json")
STATE_FILE = os.path.join(BASE, "finmind", ".local", "vshape_state.json")
EVENT_LOG = os.path.join(BASE, "logs", "vshape_events.log")
GATEWAY = "http://localhost:5455"
DEFAULT_WATCH = {"long": ["00631L"], "short": ["00631L", "0050"]}

app = Flask(__name__)

CODE_RE = re.compile(r"^[0-9]{4,6}[A-Z]?$")
_name_map = {"ts": 0.0, "data": {}}     # 名稱 → [代號]（30 分鐘快取）


class LookupUnavailable(RuntimeError):
    """查詢服務（gateway）當下不可用，和「查無此標的」要分開講。"""


def _norm(text: str) -> str:
    """大小寫、全半形、臺/台 異體字都先統一，避免明明有卻查不到。"""
    t = unicodedata.normalize("NFKC", text or "").strip().upper()
    return t.replace("臺", "台")


def _name_map_refresh() -> dict:
    """從 gateway 全市場快照建立名稱對照；partial 或失敗都不覆蓋舊資料。"""
    now = time.time()
    if _name_map["data"] and now - _name_map["ts"] < 1800:
        return _name_map["data"]
    mapping = {}
    try:
        r = requests.get(f"{GATEWAY}/market_snapshot", timeout=28)
        payload = r.json()
        if payload.get("partial"):
            return _name_map["data"]
        for it in (payload.get("items") or []):
            name = _norm(it.get("name") or "")
            code = str(it.get("code") or "").strip()
            if name and code:
                mapping.setdefault(name, []).append(code)
    except Exception:
        return _name_map["data"]
    if mapping:
        _name_map.update(ts=now, data=mapping)
    return _name_map["data"]


def resolve_code(text: str) -> tuple[str | None, list | None]:
    """代號或中文名稱 → (代號, None)；多筆符合 → (None, 候選)；服務掛掉 → 丟 LookupUnavailable。"""
    norm = _norm(text)
    if CODE_RE.match(norm):
        return norm, None

    # 先問便宜的模糊搜尋（gateway 已支援 4-6 碼 ETF）
    try:
        r = requests.get(f"{GATEWAY}/stock_search", params={"q": norm, "limit": 20}, timeout=6)
        results = (r.json() or {}).get("results") or []
    except Exception as exc:      # 查詢服務壞掉 ≠ 查無此標的
        raise LookupUnavailable("查詢服務暫時不可用，請稍後再試") from exc

    if results:
        exact = [x for x in results if _norm(x.get("name", "")) == norm]
        if len(exact) == 1:
            return exact[0]["code"], None
        if len(results) == 1:
            return results[0]["code"], None
        return None, results[:8]

    # 備援：全市場快照的名稱表（涵蓋 5 碼 ETF 等）
    hits = _name_map_refresh().get(norm, [])
    if len(hits) == 1:
        return hits[0], None
    if hits:
        return None, [{"code": c, "name": norm} for c in hits[:8]]
    return None, []


def load_watch() -> dict:
    try:
        with open(WATCH_FILE) as f:
            data = json.load(f)
        return {
            "long": [str(c) for c in (data.get("long") or [])],
            "short": [str(c) for c in (data.get("short") or [])],
        }
    except Exception:
        return {k: list(v) for k, v in DEFAULT_WATCH.items()}


def save_watch(watch: dict) -> None:
    os.makedirs(os.path.dirname(WATCH_FILE), exist_ok=True)
    with open(WATCH_FILE, "w") as f:
        json.dump(watch, f, ensure_ascii=False, indent=2)


def quotes(codes: list) -> dict:
    """向 gateway 取即時價；gateway 掛掉就回空 dict，不要讓頁面跟著壞。"""
    if not codes:
        return {}
    try:
        r = requests.get(f"{GATEWAY}/snapshot", params={"codes": ",".join(codes)}, timeout=5)
        data = r.json().get("data") or {}
        return {
            code: {
                "close": v.get("close"),
                "chg_pct": v.get("change_rate"),
            }
            for code, v in data.items()
        }
    except Exception:
        return {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {"updated": None, "items": {}}
    # 階段是昨天的殘檔時要明講，否則會跟現查的即時報價混在一起看
    upd_date = state.get("date")
    if upd_date:
        state["stale"] = upd_date != str(date.today())
    else:
        try:
            state["stale"] = date.fromtimestamp(os.path.getmtime(STATE_FILE)) != date.today()
        except OSError:
            state["stale"] = True
    # tick 斷線時 updated 仍是現在時間，用最後一筆 tick 判斷階段是否已經停止更新
    last_tick = state.get("last_tick")
    if not state["stale"] and last_tick:
        try:
            lt = datetime.combine(date.today(), datetime.strptime(last_tick, "%H:%M:%S").time())
            now = datetime.now()
            # 只在盤中判斷：收盤後 tick 本來就會停，不能當成斷線
            in_session = now.hour < 13 or (now.hour == 13 and now.minute <= 30)
            if in_session and (now - lt).total_seconds() > 360:
                state["stale"] = True
        except ValueError:
            pass
    watch = load_watch()
    state["watch"] = watch
    state["quotes"] = quotes(sorted(set(watch["long"]) | set(watch["short"])))
    return jsonify(state)


@app.route("/api/events")
def api_events():
    """今日 V 轉（long）與 A 轉（short）事件紀錄，新到舊。"""
    today = str(date.today())
    rows = []
    try:
        with open(EVENT_LOG, encoding="utf-8") as f:
            for line in f:
                if line.startswith(today):
                    rows.append(line.rstrip("\n"))
    except FileNotFoundError:
        pass
    return jsonify({"date": today, "events": list(reversed(rows[-100:]))})


@app.route("/api/watch", methods=["POST"])
def api_watch():
    body = request.get_json(silent=True) or {}
    direction = body.get("direction")
    action = body.get("action")
    raw = str(body.get("code") or "").strip()
    if direction not in ("long", "short") or action not in ("add", "remove") or not raw:
        return jsonify({"ok": False, "error": "參數錯誤"}), 400

    code, candidates = (raw, None)
    if action == "add":
        try:
            code, candidates = resolve_code(raw)
        except LookupUnavailable as e:
            return jsonify({"ok": False, "error": str(e)}), 200
        if candidates is not None:
            if candidates:
                return jsonify({"ok": False, "error": f"「{raw}」符合多筆，請選擇",
                                "candidates": candidates}), 200
            return jsonify({"ok": False, "error": f"找不到「{raw}」這個標的"}), 200
    if not CODE_RE.match(code):
        return jsonify({"ok": False, "error": "代號格式應為 4-6 位數字（ETF 可加字母，如 00631L）"}), 400

    watch = load_watch()
    if action == "add":
        if code not in watch[direction]:
            watch[direction].append(code)
    else:
        watch[direction] = [c for c in watch[direction] if c != code]
    save_watch(watch)
    return jsonify({"ok": True, "watch": watch})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5995, debug=False)
