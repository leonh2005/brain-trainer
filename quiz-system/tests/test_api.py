"""test_api: 出題、判分、歷史成績。

判分走「無狀態」設計：/api/quiz 回傳每題的 order（打散後各位置對應的原選項索引），
交卷時把 order 一起送回，後端用 order[picked] 對回原題答案。
"""

import json

import app as quiz
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(quiz, "MISTAKES_PATH", tmp_path / "mistakes.json")
    quiz.app.config["TESTING"] = True
    return quiz.app.test_client()


def _answer_all(client, items, correct: bool):
    """組出全部答對或全部答錯的提交 payload。"""
    picked = []
    for it in items:
        q = quiz.QUESTIONS[it["qid"]]
        right = it["order"].index(q["ans"])   # 打散後的正解位置
        wrong = (right + 1) % 4
        picked.append({"qid": it["qid"], "order": it["order"],
                       "picked": right if correct else wrong})
    return {"items": picked}


def test_quiz_returns_requested_count(client):
    items = client.get("/api/quiz?count=50").get_json()["items"]

    assert len(items) == 50
    assert len({it["qid"] for it in items}) == 50          # 不重複
    assert all(len(it["opts"]) == 4 for it in items)


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
