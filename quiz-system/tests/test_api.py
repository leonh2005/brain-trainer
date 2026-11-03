"""test_api: 出題、判分、歷史成績。

判分走「無狀態」設計：/api/quiz 回傳每題的 order（各顯示位置對應的原選項索引）。
選項維持 PDF 原順序，所以 order 恆為 [0,1,2,3]；欄位保留是為了交卷時
能對回原題答案（order[picked]），也讓日後恢復打散時前後端不必改協議。
"""

import json

import app as quiz
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(quiz, "MISTAKES_PATH", tmp_path / "mistakes.json")
    # 遷移是延遲執行的，這裡重置旗標，確保它用的是 tmp 路徑而不是正式資料
    monkeypatch.setattr(quiz, "_migrated", False)
    quiz.app.config["TESTING"] = True
    return quiz.app.test_client()


def _answer_all(client, items, correct: bool):
    """組出全部答對或全部答錯的提交 payload。"""
    picked = []
    for it in items:
        q = quiz.QUESTIONS[it["qid"]]
        right = it["order"].index(q["ans"])   # 正解在畫面上的位置
        wrong = (right + 1) % 4
        picked.append({"qid": it["qid"], "order": it["order"],
                       "picked": right if correct else wrong})
    return {"items": picked}


def test_quiz_returns_requested_count(client):
    items = client.get("/api/quiz?count=50").get_json()["items"]

    assert len(items) == 50
    assert len({it["qid"] for it in items}) == 50          # 不重複
    assert all(len(it["opts"]) == 4 for it in items)


def test_options_keep_pdf_order(client):
    """選項維持 PDF 原順序，不打散。

    2026-11-03：原本每次出題都 shuffle，對照 PDF 原稿時位置會跑掉。
    order 仍回 [0,1,2,3]，前端與交卷比對的協議不變。
    """
    items = client.get("/api/quiz?count=10").get_json()["items"]

    assert all(it["order"] == [0, 1, 2, 3] for it in items)
    for it in items:
        assert it["opts"] == quiz.QUESTIONS[it["qid"]]["opts"]


def test_quiz_never_leaks_answer(client):
    items = client.get("/api/quiz?count=10").get_json()["items"]

    for it in items:
        assert set(it) == {"qid", "cat", "q", "opts", "order"}
        assert sorted(it["order"]) == [0, 1, 2, 3]         # 合法排列


def test_two_quizzes_differ(client):
    a = [it["qid"] for it in client.get("/api/quiz?count=50").get_json()["items"]]
    b = [it["qid"] for it in client.get("/api/quiz?count=50").get_json()["items"]]

    assert a != b


def test_all_correct_scores_100(client):
    items = client.get("/api/quiz?count=10").get_json()["items"]

    r = client.post("/api/submit", json=_answer_all(client, items, correct=True)).get_json()

    assert r["correct"] == 10
    assert r["score"] == 100


def test_all_wrong_scores_0(client):
    items = client.get("/api/quiz?count=10").get_json()["items"]

    r = client.post("/api/submit", json=_answer_all(client, items, correct=False)).get_json()

    assert r["correct"] == 0
    assert r["score"] == 0


def test_unanswered_counts_as_wrong(client):
    items = client.get("/api/quiz?count=4").get_json()["items"]
    payload = {"items": [{"qid": it["qid"], "order": it["order"], "picked": None}
                         for it in items]}

    r = client.post("/api/submit", json=payload).get_json()

    assert r["correct"] == 0
    assert all(d["picked"] is None for d in r["details"])


def test_unanswered_not_added_to_mistakes(client):
    """沒作答要算錯（分數），但不能進錯題庫。

    2026-11-03：測驗中途離開會送出 picked=None，整個 50 題被記成錯題，
    錯題庫從 0 暴增到 118 題，重刷時全是沒看過的題目。
    """
    items = client.get("/api/quiz?count=4").get_json()["items"]
    payload = {"items": [{"qid": it["qid"], "order": it["order"], "picked": None}
                         for it in items]}
    client.post("/api/submit", json=payload)

    assert client.get("/api/quiz?mode=mistakes&count=50").get_json()["items"] == []


def test_wrong_answer_still_added_to_mistakes(client):
    """對照組：真的有作答但答錯，仍然要進錯題庫。"""
    items = client.get("/api/quiz?count=3").get_json()["items"]
    client.post("/api/submit", json=_answer_all(client, items, correct=False))

    got = {q["qid"] for q in client.get("/api/quiz?mode=mistakes&count=50").get_json()["items"]}
    assert got == {it["qid"] for it in items}


def test_mixed_answered_and_unanswered(client):
    """同一份提交裡，只有真的答錯的進錯題庫，未作答的不進。

    全部未作答／全部答錯的測試無法區分「逐題過濾」與「整份過濾」。
    """
    items = client.get("/api/quiz?count=2").get_json()["items"]
    right = items[0]["order"].index(quiz.QUESTIONS[items[0]["qid"]]["ans"])
    payload = {"items": [
        {"qid": items[0]["qid"], "order": items[0]["order"], "picked": (right + 1) % 4},
        {"qid": items[1]["qid"], "order": items[1]["order"], "picked": None},
    ]}

    r = client.post("/api/submit", json=payload).get_json()

    assert r["ok"] is True
    got = {q["qid"] for q in client.get("/api/quiz?mode=mistakes&count=50").get_json()["items"]}
    assert got == {items[0]["qid"]}


def test_unanswered_submit_succeeds(client):
    """交卷本身要成功（4xx 時錯題庫自然是空的，不能誤判成過關）。"""
    items = client.get("/api/quiz?count=4").get_json()["items"]
    payload = {"items": [{"qid": it["qid"], "order": it["order"], "picked": None}
                         for it in items]}

    r = client.post("/api/submit", json=payload)

    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_migration_skips_unanswered(tmp_path, monkeypatch):
    """舊資料遷移也要排除沒作答的題目（與交卷結算同一條規則）。"""
    hist = [{"at": "2026-01-01 10:00:00", "score": 0, "correct": 0, "total": 2,
             "items": [{"qid": quiz.QUESTIONS[10]["id"], "correct": False, "picked": None},
                       {"qid": quiz.QUESTIONS[11]["id"], "correct": False, "picked": 2}]}]
    monkeypatch.setattr(quiz, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(quiz, "MISTAKES_PATH", tmp_path / "mistakes.json")
    monkeypatch.setattr(quiz, "_migrated", False)
    (tmp_path / "history.json").write_text(json.dumps(hist), encoding="utf-8")

    quiz._ensure_migrated()

    assert quiz._load_mistakes() == {quiz.QUESTIONS[11]["id"]}


def _isolated(tmp_path, monkeypatch, history: list) -> None:
    """把路徑指向 tmp 並寫入指定 history，重置遷移旗標。"""
    monkeypatch.setattr(quiz, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(quiz, "MISTAKES_PATH", tmp_path / "mistakes.json")
    monkeypatch.setattr(quiz, "_migrated", False)
    (tmp_path / "history.json").write_text(json.dumps(history), encoding="utf-8")


def test_history_triggers_migration(tmp_path, monkeypatch):
    """開頁時 /api/history 就要觸發遷移。

    否則 mistake_count 是 0，前端把「錯題重刷」按鈕設成 disabled，
    使用者點不進錯題模式，也就永遠不會觸發遷移 —— 死結。
    """
    qid = quiz.QUESTIONS[10]["id"]
    _isolated(tmp_path, monkeypatch, [
        {"at": "2026-01-01 10:00:00", "score": 0, "correct": 0, "total": 1,
         "items": [{"qid": qid, "correct": False, "picked": 2}]}])

    d = quiz.app.test_client().get("/api/history").get_json()

    assert d["mistake_count"] == 1


def test_migration_retries_after_failure(tmp_path, monkeypatch):
    """遷移失敗不可把旗標鎖死，否則該 process 終身不再重試。"""
    monkeypatch.setattr(quiz, "MISTAKES_PATH", tmp_path / "mistakes.json")
    monkeypatch.setattr(quiz, "_migrated", False)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(quiz, "_migrate_mistakes_once", boom)

    quiz._ensure_migrated()
    quiz._ensure_migrated()

    assert len(calls) == 2


def test_migration_tolerates_malformed_items(tmp_path, monkeypatch):
    """history 有壞掉的 items 時跳過該筆，不可讓整段遷移永遠失敗。"""
    good = quiz.QUESTIONS[10]["id"]
    _isolated(tmp_path, monkeypatch, [
        {"at": "2026-01-01 10:00:00", "items": [
            None, 0, "x", {"qid": "abc"}, {"qid": True},
            {"qid": good, "correct": False, "picked": 1},
        ]}])

    quiz._ensure_migrated()

    assert quiz._load_mistakes() == {good}


def test_mistakes_count_capped_to_submit_limit(client, monkeypatch):
    """錯題模式也要夾到交卷上限，不能發出自己會拒收的考卷。"""
    items = client.get("/api/quiz?count=5").get_json()["items"]
    client.post("/api/submit", json=_answer_all(client, items, correct=False))

    monkeypatch.setattr(quiz, "MAX_ITEMS", 3)   # 模擬錯題數超過上限
    got = client.get("/api/quiz?mode=mistakes&count=50").get_json()["items"]

    assert len(got) <= 3


def test_submit_records_per_question_details(client):
    """錯題重刷的資料基礎：每題的 qid 與對錯都要留下來。"""
    items = client.get("/api/quiz?count=5").get_json()["items"]

    r = client.post("/api/submit", json=_answer_all(client, items, correct=True)).get_json()

    assert len(r["details"]) == 5
    assert {d["qid"] for d in r["details"]} == {it["qid"] for it in items}
    assert all(d["correct"] for d in r["details"])


def test_submit_appends_history(client, tmp_path):
    items = client.get("/api/quiz?count=5").get_json()["items"]
    client.post("/api/submit", json=_answer_all(client, items, correct=True))

    records = client.get("/api/history").get_json()["records"]

    assert len(records) == 1
    assert records[0]["score"] == 100
    assert records[0]["total"] == 5
    stored = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(stored[0]["items"]) == 5      # 每題明細留在檔案，API 只回摘要


def test_count_above_bank_is_rejected(client):
    assert client.get("/api/quiz?count=999").status_code == 400


def test_count_zero_is_rejected(client):
    assert client.get("/api/quiz?count=0").status_code == 400


def test_empty_submit_is_rejected(client):
    assert client.post("/api/submit", json={"items": []}).status_code == 400


def test_unknown_qid_is_rejected(client):
    r = client.post("/api/submit", json={"items": [{"qid": 99999, "order": [0, 1, 2, 3],
                                                    "picked": 0}]})
    assert r.status_code == 400


def _submit_some(client, items, results):
    """results: {qid: bool} —— 指定每題要答對（True）或答錯（False）。"""
    picked = []
    for it in items:
        right = it["order"].index(quiz.QUESTIONS[it["qid"]]["ans"])
        ok = results.get(it["qid"], True)
        picked.append({"qid": it["qid"], "order": it["order"],
                       "picked": right if ok else (right + 1) % 4})
    client.post("/api/submit", json={"items": picked})


def test_mistakes_mode_returns_only_wrong_questions(client):
    items = client.get("/api/quiz?count=10").get_json()["items"]
    wrong = {it["qid"] for it in items[:3]}
    _submit_some(client, items, {it["qid"]: it["qid"] not in wrong for it in items})

    r = client.get("/api/quiz?mode=mistakes&count=50").get_json()

    assert {it["qid"] for it in r["items"]} == wrong


def test_mistakes_accumulate_and_never_drop_out(client):
    """答對不會把題目移出錯題庫：錯題庫只增不減。"""
    first = client.get("/api/quiz?count=10").get_json()["items"]
    wrong1 = {it["qid"] for it in first[:3]}
    _submit_some(client, first, {it["qid"]: it["qid"] not in wrong1 for it in first})

    # 把剛才錯的題全部答對
    retry = client.get("/api/quiz?mode=mistakes&count=50").get_json()["items"]
    _submit_some(client, retry, {it["qid"]: True for it in retry})

    # 之後又答錯新的一題
    second = client.get("/api/quiz?count=10").get_json()["items"]
    new_wrong = second[0]["qid"]
    _submit_some(client, second, {it["qid"]: it["qid"] != new_wrong for it in second})

    r = client.get("/api/quiz?mode=mistakes&count=50").get_json()

    assert {it["qid"] for it in r["items"]} == wrong1 | {new_wrong}


def test_mistakes_mode_is_empty_before_any_submit(client):
    r = client.get("/api/quiz?mode=mistakes&count=50").get_json()

    assert r["ok"] is True
    assert r["items"] == []


def test_history_reports_mistake_count(client):
    items = client.get("/api/quiz?count=10").get_json()["items"]
    wrong = {it["qid"] for it in items[:4]}
    _submit_some(client, items, {it["qid"]: it["qid"] not in wrong for it in items})

    assert client.get("/api/history").get_json()["mistake_count"] == 4


def test_mistakes_count_larger_than_bank_is_clamped(client):
    """前端固定送 count=50，錯題只有幾題時要全出而不是報錯。"""
    items = client.get("/api/quiz?count=10").get_json()["items"]
    wrong = {it["qid"] for it in items[:2]}
    _submit_some(client, items, {it["qid"]: it["qid"] not in wrong for it in items})

    r = client.get("/api/quiz?mode=mistakes&count=50").get_json()

    assert len(r["items"]) == 2


def test_mistakes_are_stored_separately_from_history(client, tmp_path):
    """錯題庫獨立存檔，不會被 history 的筆數上限擠掉。"""
    items = client.get("/api/quiz?count=3").get_json()["items"]
    wrong = {it["qid"] for it in items}
    _submit_some(client, items, {it["qid"]: False for it in items})

    stored = json.loads((tmp_path / "mistakes.json").read_text(encoding="utf-8"))

    assert set(stored) == wrong


def test_mistakes_survive_history_rotation(client, monkeypatch):
    items = client.get("/api/quiz?count=3").get_json()["items"]
    wrong = {it["qid"] for it in items}
    _submit_some(client, items, {it["qid"]: False for it in items})

    # 之後大量交卷，讓舊紀錄被 HISTORY_LIMIT 擠出
    monkeypatch.setattr(quiz, "HISTORY_LIMIT", 2)
    for _ in range(6):
        good = client.get("/api/quiz?count=1").get_json()["items"]
        _submit_some(client, good, {good[0]["qid"]: True})

    assert wrong <= quiz._mistake_ids()


def test_payload_must_be_object(client):
    assert client.post("/api/submit", json=[1, 2]).status_code == 400


def test_bool_is_not_accepted_as_qid(client):
    payload = {"items": [{"qid": True, "order": [0, 1, 2, 3], "picked": 0}]}

    assert client.post("/api/submit", json=payload).status_code == 400


def test_duplicate_qid_rejected(client):
    it = client.get("/api/quiz?count=1").get_json()["items"][0]
    payload = {"items": [{"qid": it["qid"], "order": it["order"], "picked": 0},
                         {"qid": it["qid"], "order": it["order"], "picked": 1}]}

    assert client.post("/api/submit", json=payload).status_code == 400


def test_too_many_items_rejected(client):
    one = {"qid": 0, "order": [0, 1, 2, 3], "picked": 0}
    payload = {"items": [dict(one, qid=i) for i in range(quiz.MAX_ITEMS + 1)]}

    assert client.post("/api/submit", json=payload).status_code == 400


def test_health(client):
    assert client.get("/api/health").get_json() == {"status": "ok"}
