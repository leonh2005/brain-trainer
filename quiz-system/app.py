"""技術士「電腦軟體應用」丙級學科題庫測驗（port 5380）。

GET  /api/quiz?count=50  隨機抽題（選項一併打散），不含答案
POST /api/submit         交卷判分，寫入 history.json
GET  /api/history        最近成績（含每題對錯，供之後的錯題重刷使用）
GET  /api/health
"""

import json
import os
import random
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
QUESTIONS_PATH = Path(os.environ.get("QUIZ_QUESTIONS", BASE_DIR / "questions.json"))
HISTORY_PATH = Path(os.environ.get("QUIZ_HISTORY", BASE_DIR / "history.json"))
HISTORY_LIMIT = 200  # 只留最近 N 份，避免檔案無限長大

app = Flask(__name__)
QUESTIONS = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
RNG = random.SystemRandom()


def _load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_history(records: list[dict]) -> None:
    HISTORY_PATH.write_text(json.dumps(records[-HISTORY_LIMIT:], ensure_ascii=False),
                            encoding="utf-8")


def _mistake_ids() -> set[int]:
    """所有曾經答錯過的題目。只累積、不移出——答對過也留在錯題庫裡。"""
    ids = set()
    for rec in _load_history():
        for it in rec.get("items", []):
            if not it.get("correct"):
                ids.add(it["qid"])
    return ids


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/quiz")
def quiz():
    mode = request.args.get("mode", "all")
    if mode not in ("all", "mistakes"):
        return jsonify({"ok": False, "error": "mode 需為 all 或 mistakes"}), 400
    try:
        count = int(request.args.get("count", 50))
    except ValueError:
        return jsonify({"ok": False, "error": "count 必須是整數"}), 400
    if count < 1:
        return jsonify({"ok": False, "error": "count 需大於 0"}), 400

    if mode == "mistakes":
        mistakes = _mistake_ids()
        pool = [q for q in QUESTIONS if q["id"] in mistakes]
        count = min(count, len(pool))   # 錯題不足時全出，不報錯
    else:
        pool = QUESTIONS
        if count > len(pool):
            return jsonify({"ok": False, "error": f"count 需介於 1 與 {len(pool)}"}), 400

    items = []
    for q in RNG.sample(pool, count):
        order = [0, 1, 2, 3]
        RNG.shuffle(order)          # 打散選項，避免正解固定在同一個位置
        items.append({
            "qid": q["id"],
            "cat": q["cat"],
            "q": q["q"],
            "opts": [q["opts"][i] for i in order],
            "order": order,
        })
    return jsonify({"ok": True, "mode": mode, "items": items})


@app.post("/api/submit")
def submit():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "items 不可為空"}), 400

    details = []
    for it in items:
        try:
            qid = int(it["qid"])
            order = [int(x) for x in it["order"]]
            raw_picked = it.get("picked")
            picked = None if raw_picked is None else int(raw_picked)
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "items 格式不正確"}), 400

        if not 0 <= qid < len(QUESTIONS):
            return jsonify({"ok": False, "error": f"未知題號 {qid}"}), 400
        if sorted(order) != [0, 1, 2, 3]:
            return jsonify({"ok": False, "error": "order 必須是 0-3 的排列"}), 400
        if picked is not None and not 0 <= picked < 4:
            return jsonify({"ok": False, "error": "picked 需為 0-3 或 null"}), 400

        q = QUESTIONS[qid]
        details.append({
            "qid": qid,
            "correct": picked is not None and order[picked] == q["ans"],
            "picked": picked,
            "answer_index": order.index(q["ans"]),
        })

    total = len(details)
    n_correct = sum(1 for d in details if d["correct"])
    score = round(n_correct / total * 100)
    at = time.strftime("%Y-%m-%d %H:%M:%S")

    records = _load_history()
    records.append({
        "at": at,
        "score": score,
        "correct": n_correct,
        "total": total,
        # 每題 qid 與對錯：之後「只刷錯題」靠這份資料，不用改格式
        "items": [{"qid": d["qid"], "correct": d["correct"], "picked": d["picked"]}
                  for d in details],
    })
    _save_history(records)

    return jsonify({"ok": True, "score": score, "correct": n_correct, "total": total,
                    "at": at, "details": details})


@app.get("/api/history")
def history():
    try:
        limit = max(1, int(request.args.get("limit", 10)))
    except ValueError:
        limit = 10
    records = _load_history()
    return jsonify({"ok": True,
                    "records": list(reversed(records[-limit:])),
                    "mistake_count": len(_mistake_ids())})


@app.get("/")
def index():
    return render_template("index.html", total_questions=len(QUESTIONS))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5380, debug=False)
