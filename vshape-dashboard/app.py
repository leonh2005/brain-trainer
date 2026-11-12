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
from datetime import date, datetime

import requests
from flask import Flask, jsonify, render_template, request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH_FILE = os.path.join(BASE, "finmind", ".local", "vshape_watch.json")
STATE_FILE = os.path.join(BASE, "finmind", ".local", "vshape_state.json")
GATEWAY = "http://localhost:5455"
DEFAULT_WATCH = {"long": ["00631L"], "short": ["00631L", "0050"]}

app = Flask(__name__)

CODE_RE = re.compile(r"^[0-9]{4,6}[A-Z]?$")
_name_map = {"ts": 0.0, "data": {}}     # 名稱 → 代號（30 分鐘快取）


def _name_map_refresh() -> dict:
    """從 gateway 全市場快照建立名稱對照；gateway 掛掉就沿用舊資料。"""
    now = time.time()
    if _name_map["data"] and now - _name_map["ts"] < 1800:
        return _name_map["data"]
    mapping = {}
    try:
        r = requests.get(f"{GATEWAY}/market_snapshot", timeout=25)
        for it in (r.json().get("items") or []):
            name = str(it.get("name") or "").strip()
            code = str(it.get("code") or "").strip()
            if name and code:
                mapping[name] = code
    except Exception:
        pass
    if mapping:
        _name_map.update(ts=now, data=mapping)
    return _name_map["data"]


def resolve_code(text: str):
    """代號或中文名稱 → (代號, None)；對到多個 → (None, 候選清單)。"""
    text = text.strip()
    if CODE_RE.match(text):
        return text, None

    mapping = _name_map_refresh()
    if mapping:
        exact = [c for n, c in mapping.items() if n == text]
        if len(exact) == 1:
            return exact[0], None
        part = [(c, n) for n, c in mapping.items() if text in n]
        if len(part) == 1:
            return part[0][0], None
        if part:
            return None, [{"code": c, "name": n} for c, n in part[:8]]

    try:      # 備援：gateway 模糊搜尋（只涵蓋 4 碼個股）
        r = requests.get(f"{GATEWAY}/stock_search", params={"q": text}, timeout=6)
        res = r.json().get("results") or []
        if len(res) == 1:
            return res[0]["code"], None
        if res:
            return None, res[:8]
    except Exception:
        pass
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
        code, candidates = resolve_code(raw)
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
