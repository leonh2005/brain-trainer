"""技術士「電腦軟體應用」丙級學科題庫測驗（port 5380）。

GET  /api/quiz?count=50  隨機抽題（選項一併打散），不含答案
POST /api/submit         交卷判分，寫入 history.json
GET  /api/history        最近成績（含每題對錯，供之後的錯題重刷使用）
GET  /api/health
"""

import json
import os
import random
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
QUESTIONS_PATH = Path(os.environ.get("QUIZ_QUESTIONS", BASE_DIR / "questions.json"))
HISTORY_PATH = Path(os.environ.get("QUIZ_HISTORY", BASE_DIR / "history.json"))
MISTAKES_PATH = Path(os.environ.get("QUIZ_MISTAKES", BASE_DIR / "mistakes.json"))
HISTORY_LIMIT = 200   # 只留最近 N 份成績
MAX_ITEMS = 200       # 單次交卷題數上限，避免巨型 payload 寫爆 history

app = Flask(__name__)
QUESTIONS = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
RNG = random.SystemRandom()
_WRITE_LOCK = threading.Lock()   # read-modify-write 必須原子，否則併發交卷會互蓋


def _load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_history(records: list[dict]) -> None:
    _atomic_write(HISTORY_PATH, json.dumps(records[-HISTORY_LIMIT:], ensure_ascii=False))


def _atomic_write(path: Path, text: str) -> None:
    """先寫暫存檔再 os.replace：避免讀到寫到一半的檔案。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_mistakes() -> set[int]:
    """錯題庫：只增不減，與 HISTORY_LIMIT 無關。

    檔案不存在時從既有 history 匯入一次（相容舊資料）。
    """
    if MISTAKES_PATH.exists():
        try:
            return set(json.loads(MISTAKES_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return set()
    ids = set()
    for rec in _load_history():
        for it in rec.get("items", []):
            if not it.get("correct"):
                ids.add(it["qid"])
    if ids:
        _save_mistakes(ids)   # 首次匯入就落地：只靠 history 推算會隨 200 筆輪替消失
    return ids


def _save_mistakes(ids: set[int]) -> None:
    _atomic_write(MISTAKES_PATH, json.dumps(sorted(ids)))


def _mistake_ids() -> set[int]:
    """所有曾經答錯過的題目。只累積、不移出——答對過也留在錯題庫裡。"""
    return _load_mistakes()


def _as_int(value, name: str) -> int:
    """嚴格整數轉型：bool、字串、非整數 float 一律拒絕，避免 True → 1 這種誤判。"""
    if isinstance(value, bool):
        raise ValueError(f"{name} 格式不正確")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"{name} 格式不正確")


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
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "payload 必須是 JSON 物件"}), 400
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "items 不可為空"}), 400
    if len(items) > MAX_ITEMS:
        return jsonify({"ok": False, "error": f"單次最多 {MAX_ITEMS} 題"}), 400

    details = []
    seen: set[int] = set()
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("order"), list):
            return jsonify({"ok": False, "error": "items 格式不正確"}), 400
        try:
            qid = _as_int(it["qid"], "qid")
            order = [_as_int(x, "order") for x in it["order"]]
            raw_picked = it.get("picked")
            picked = None if raw_picked is None else _as_int(raw_picked, "picked")
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "items 格式不正確"}), 400

        if qid in seen:
            return jsonify({"ok": False, "error": f"題號 {qid} 重複"}), 400
        seen.add(qid)
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

    with _WRITE_LOCK:
        records = _load_history()
        records.append({
            "at": at,
            "score": score,
            "correct": n_correct,
            "total": total,
            # 每題 qid 與對錯：錯題重刷靠這份資料
            "items": [{"qid": d["qid"], "correct": d["correct"], "picked": d["picked"]}
                      for d in details],
        })
        _save_history(records)
        # 錯題庫獨立存檔、只增不減，不會被 history 的 200 筆上限擠掉
        mistakes = _load_mistakes()
        mistakes.update(d["qid"] for d in details if not d["correct"])
        _save_mistakes(mistakes)

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
