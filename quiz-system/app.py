"""技術士「電腦軟體應用」丙級學科題庫測驗（port 5380）。

GET  /api/quiz?count=50  隨機抽題（選項維持 PDF 原順序），不含答案
POST /api/submit         交卷判分，寫入 history.json
GET  /api/history        最近成績（含每題對錯，供之後的錯題重刷使用）
GET  /api/health
"""

import json
import os
import random
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
QUESTIONS_PATH = Path(os.environ.get("QUIZ_QUESTIONS", BASE_DIR / "questions.json"))
HISTORY_PATH = Path(os.environ.get("QUIZ_HISTORY", BASE_DIR / "history.json"))
MISTAKES_PATH = Path(os.environ.get("QUIZ_MISTAKES", BASE_DIR / "mistakes.json"))
HISTORY_LIMIT = 200   # 只留最近 N 份成績
# 答對累計達此次數即視為熟練，不再出現在一般出題（獨立計數，不受 history 200 筆上限影響）
MASTERED_THRESHOLD = 10
STATS_PATH = Path(os.environ.get("QUIZ_STATS", BASE_DIR / "stats.json"))

MAX_ITEMS = 200       # 單次交卷題數上限，避免巨型 payload 寫爆 history

app = Flask(__name__)
QUESTIONS = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
RNG = random.SystemRandom()
_WRITE_LOCK = threading.Lock()   # read-modify-write 必須原子，否則併發交卷會互蓋


def _quarantine(path: Path) -> None:
    """損壞的狀態檔改名保留，避免下一筆寫入直接把舊資料蓋掉。"""
    try:
        path.replace(path.with_suffix(path.suffix + ".corrupt"))
    except OSError:
        pass


def _read_json_list(path: Path) -> list | None:
    """讀 JSON 陣列：不存在回 []、損壞或型別不對回 None（呼叫端據此避免覆寫）。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _quarantine(path)
        return None
    if not isinstance(data, list):
        _quarantine(path)
        return None
    return data


def _load_history() -> list[dict]:
    data = _read_json_list(HISTORY_PATH)
    return [r for r in (data or []) if isinstance(r, dict)]


def _save_history(records: list[dict]) -> None:
    _atomic_write(HISTORY_PATH, json.dumps(records[-HISTORY_LIMIT:], ensure_ascii=False))


def _load_stats() -> dict[int, int]:
    """每題累計答對次數 {qid: count}。壞檔視為空，不讓統計擋住練習。"""
    if not STATS_PATH.exists():
        return {}
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("correct_counts") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _save_stats(counts: dict[int, int]) -> None:
    _atomic_write(STATS_PATH, json.dumps(
        {"correct_counts": {str(k): v for k, v in counts.items()}}, ensure_ascii=False))


def _atomic_write(path: Path, text: str) -> None:
    """先寫唯一暫存檔再 os.replace：併發寫入不會互踩，中斷也不會留下半截檔。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load_mistakes() -> set[int]:
    """錯題庫：只增不減，與 HISTORY_LIMIT 無關。"""
    data = _read_json_list(MISTAKES_PATH)
    return {x for x in (data or []) if isinstance(x, int) and not isinstance(x, bool)}


def _save_mistakes(ids: set[int]) -> None:
    _atomic_write(MISTAKES_PATH, json.dumps(sorted(ids)))


# 進位題的判定關鍵字。只用「進位」會漏掉 10 題純進制／補數題
# （例如「十進制數的17等於十六進制數」、Q43「2 的補數」），
# 三個都收目前共 32 題。
_CARRY_KEYS = ("進位", "進制", "補數")


def _is_carry(q: dict) -> bool:
    """進位／進制／補數相關題目。

    題庫沒有專門分類，只能比對題目與選項的關鍵字。
    """
    text = q["q"] + "".join(q["opts"])
    return any(k in text for k in _CARRY_KEYS)


def _counts_as_mistake(correct: bool, picked) -> bool:
    """答錯「且真的有作答」才算錯題。

    picked 為 None 是沒作答（中途離開／逾時送出）；把它記進錯題庫，
    會讓一次空測驗把整份 50 題都變成錯題。注意 picked == 0 是選了
    第一個選項，不能當成 falsy 處理掉。
    """
    return not correct and picked is not None


def _migrate_mistakes_once() -> None:
    """舊版的錯題存在 history 裡，第一次需要時搬到 mistakes.json。

    用獨立的 marker 檔當完成標記，不用「mistakes.json 存在」判斷：
    遷移寫檔失敗後，submit 仍可能建立一份空檔，那樣重試就永遠 no-op、
    歷史錯題靜默消失。
    """
    marker = MISTAKES_PATH.with_name(MISTAKES_PATH.name + ".migrated")
    if marker.exists():
        return
    with _WRITE_LOCK:
        if marker.exists():
            return
        if MISTAKES_PATH.exists():
            # 已經有錯題庫（舊版遷移過或使用者已有資料），視為完成
            marker.touch()
            return
        ids = set()
        for rec in _load_history():
            for it in rec.get("items") or []:
                if not isinstance(it, dict):
                    continue
                qid = it.get("qid")
                if not isinstance(qid, int) or isinstance(qid, bool):
                    continue
                if _counts_as_mistake(it.get("correct"), it.get("picked")):
                    ids.add(qid)
        if ids:
            _save_mistakes(ids)
            print(f"  錯題庫遷移：從 history 匯入 {len(ids)} 題")
        marker.touch()


def _mistake_ids() -> set[int]:
    """所有曾經答錯過的題目。只累積、不移出——答對過也留在錯題庫裡。"""
    return _load_mistakes()


_migrated = False


def _ensure_migrated() -> None:
    """延遲遷移：第一次真的需要錯題庫時才做。

    不能在 import 時執行 —— 測試 import 這個模組時 fixture 還沒把路徑
    換成 tmp，會直接讀寫正式資料（刪掉 mistakes.json 後跑 pytest 會
    拿真實 history 把它重建回來）。
    """
    global _migrated
    if _migrated:
        return
    try:
        _migrate_mistakes_once()
    except Exception:      # 遷移失敗不該讓請求失敗；不設旗標，下次再試
        return
    _migrated = True


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
    if mode not in ("all", "mistakes", "carry", "unmastered"):
        return jsonify({"ok": False, "error": "mode 需為 all、mistakes、carry 或 unmastered"}), 400
    try:
        count = int(request.args.get("count", 50))
    except ValueError:
        return jsonify({"ok": False, "error": "count 必須是整數"}), 400
    if count < 1:
        return jsonify({"ok": False, "error": "count 需大於 0"}), 400

    if mode == "mistakes":
        _ensure_migrated()
        mistakes = _mistake_ids()
        pool = [q for q in QUESTIONS if q["id"] in mistakes]
        # 除了錯題數量，也要夾到交卷上限，否則會發出 /api/submit 自己會拒收的考卷
        count = min(count, len(pool), MAX_ITEMS)
    elif mode == "carry":
        pool = [q for q in QUESTIONS if _is_carry(q)]
        count = min(count, len(pool), MAX_ITEMS)
    elif mode == "unmastered":
        # 已答對達 MASTERED_THRESHOLD 次的題目視為熟練，從一般練習中移除
        stats = _load_stats()
        pool = [q for q in QUESTIONS
                if stats.get(q["id"], 0) < MASTERED_THRESHOLD]
        count = min(count, len(pool), MAX_ITEMS)
    else:
        pool = QUESTIONS
        limit = min(len(pool), MAX_ITEMS)   # 出題上限與交卷上限一致，避免發出交不回來的考卷
        if count > limit:
            return jsonify({"ok": False, "error": f"count 需介於 1 與 {limit}"}), 400

    items = []
    for q in RNG.sample(pool, count):
        # 選項維持 PDF 原順序（不一開始是為了避免背位置，但 Steven 要看原稿對照）；
        # order 欄位仍保留 [0,1,2,3]，前端與交卷比對的協議不變。
        order = [0, 1, 2, 3]
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
        if not isinstance(it, dict) or not isinstance(it.get("order"), list) \
                or len(it["order"]) != 4:
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
    score = int(n_correct / total * 100 + 0.5)   # 四捨五入，不用 round() 的銀行家捨入
    at = time.strftime("%Y-%m-%d %H:%M:%S")

    # 遷移要在取鎖之前跑：_migrate_mistakes_once 內部自己會取 _WRITE_LOCK，
    # 而它是不可重入的 Lock，在這裡面呼叫會直接死鎖。
    _ensure_migrated()
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
        # 錯題庫獨立存檔、只增不減，不會被 history 的 200 筆上限擠掉。
        # 只收「有作答但答錯」：picked is None 是沒作答（中途離開／逾時送出），
        # 記進錯題庫會把根本沒看過的題目也算成錯題。
        mistakes = _load_mistakes()
        mistakes.update(d["qid"] for d in details
                        if _counts_as_mistake(d["correct"], d["picked"]))
        _save_mistakes(mistakes)

        # 累計答對次數（獨立檔，不受 history 200 筆上限影響）
        stats = _load_stats()
        for d in details:
            if d["correct"]:
                stats[d["qid"]] = stats.get(d["qid"], 0) + 1
        _save_stats(stats)

    return jsonify({"ok": True, "score": score, "correct": n_correct, "total": total,
                    "at": at, "details": details})


@app.post("/api/mark")
def mark():
    """標記／取消標記「不確定」的題目（marked 預設 true）。

    與答錯共用同一份 mistakes.json：練習當下按鈕就能記，不必先交卷，
    中途離開也不會漏掉。重複標記是幂等的；marked=false 可取消誤按。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "payload 必須是 JSON 物件"}), 400
    try:
        qid = _as_int(payload.get("qid"), "qid")
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    # QUESTIONS 是 list、id 等於索引（0..len-1），所以用範圍檢查而非 in
    if not 0 <= qid < len(QUESTIONS):
        return jsonify({"ok": False, "error": f"未知題號 {qid}"}), 400
    marked = payload.get("marked", True)
    if not isinstance(marked, bool):
        return jsonify({"ok": False, "error": "marked 需為 true 或 false"}), 400

    _ensure_migrated()          # 取鎖前先遷移，避免與內層鎖互卡
    with _WRITE_LOCK:
        mistakes = _load_mistakes()
        if marked:
            mistakes.add(qid)
        else:
            mistakes.discard(qid)      # 誤按可取消，不讓錯題庫只能進不能出
        _save_mistakes(mistakes)
        return jsonify({"ok": True, "qid": qid, "marked": marked,
                        "mistake_count": len(mistakes)})


@app.get("/api/history")
def history():
    try:
        limit = max(1, int(request.args.get("limit", 10)))
    except ValueError:
        limit = 10
    _ensure_migrated()      # 開頁就要正確的錯題數，否則按鈕被禁用後永遠進不去
    records = _load_history()
    # 只回摘要：每題 items 留在檔案裡給錯題庫用，不必每次開頁序列化上萬筆
    summary = [{k: r.get(k) for k in ("at", "score", "correct", "total")}
               for r in records[-limit:]]
    return jsonify({"ok": True,
                    "records": list(reversed(summary)),
                    "mistake_count": len(_mistake_ids())})


@app.get("/")
def index():
    return render_template("index.html", total_questions=len(QUESTIONS))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5380, debug=False)
