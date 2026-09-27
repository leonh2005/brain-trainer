import json

import app as app_module
import pytest

from learn_system import db, tutor
from app import create_app

# test_code 依題目定義是 pytest 斷言（見 tutor.QUESTION_PROMPT），故一律寫成
# 測試函式：模組層級的裸 assert pytest 不會收集，等於沒有測試可跑。
TEST_CODE = "from solution import add\ndef test_add():\n    assert add(1, 2) == 3"
CORRECT_ANSWER = "def add(a, b):\n    return a + b"
WRONG_ANSWER = "def add(a, b):\n    return a - b"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_spawn_map_generation",
                        lambda flask_app, did: app_module._generate_map(did))
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": "d"}],
    })
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def make_question(client, cid, monkeypatch, goal_type, payload, reference):
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": payload, "reference_answer": reference})
    return client.post(f"/api/concepts/{cid}/questions", json={"goal_type": goal_type}).get_json()["question"]["id"]


def _capture_grade_prompts(monkeypatch):
    """讓批改真的走完 tutor.grade_answer（提示詞真的被組出來），並回傳提示詞清單。"""
    prompts = []

    def fake_agent(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"verdict": "correct", "feedback": "f", "root_cause": None}), None

    monkeypatch.setattr(tutor, "_call_agent", fake_agent)
    return prompts


def test_write_answer_graded_by_real_execution(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": CORRECT_ANSWER})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_write_answer_wrong_when_test_fails(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER})
    assert r.get_json()["attempt"]["verdict"] == "wrong"


def test_wrong_write_answer_feedback_shows_the_failure(ctx, monkeypatch):
    """答錯時要看到 pytest 的失敗報告。

    pytest 把失敗報告寫在 stdout、stderr 是空的，只讀 stderr 會讓每一次答錯
    都只顯示「測試失敗：」後面沒有任何線索，學習者無從修正。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    attempt = client.post(f"/api/questions/{qid}/answer",
                          json={"answer": WRONG_ANSWER}).get_json()["attempt"]
    assert "test_add" in attempt["feedback"]


def test_pytest_stderr_noise_does_not_hide_the_report(ctx, monkeypatch):
    """stderr 有內容時仍要顯示 stdout 的報告，不能被 stderr 蓋掉。

    pytest 預設連 fd 一起捕捉，所以學習者自己 print(..., file=sys.stderr) 不會
    讓 stderr 有內容（實測 5 種答錯形狀）。唯一會有的情況是學習者的程式碼觸發
    SystemExit：stderr 只有一行 "mainloop: caught unexpected SystemExit!"，
    真正的報告（指到學習者那一行、以及「沒有測試跑過」）在 stdout。若讓 stderr
    優先，學習者就只看到那一行雜訊。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    attempt = client.post(f"/api/questions/{qid}/answer",
                          json={"answer": "import sys\nsys.exit(0)"}).get_json()["attempt"]
    assert attempt["verdict"] == "wrong"
    assert "no tests ran" in attempt["feedback"]


def test_bare_assert_test_code_grades_both_ways(ctx, monkeypatch):
    """模組層級的裸 assert 是提示詞的自然讀法，也是本任務 brief 的寫法。

    pytest 不會收集模組層級的 assert，若不處理，write 題會在建立時全數被
    422 擋掉；包成測試函式後兩種寫法都要能正常批改。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "from solution import add\nassert add(1, 2) == 3"},
                        CORRECT_ANSWER)
    right = client.post(f"/api/questions/{qid}/answer", json={"answer": CORRECT_ANSWER})
    assert right.get_json()["attempt"]["verdict"] == "correct"
    wrong = client.post(f"/api/questions/{qid}/answer", json={"answer": WRONG_ANSWER})
    assert wrong.get_json()["attempt"]["verdict"] == "wrong"


def test_write_answer_that_exits_the_process_is_not_correct(ctx, monkeypatch):
    """結束碼不可信：這些答案的行程結束碼都是 0，斷言一次都沒跑。

    `sys.exit(0)`／`raise SystemExit(0)` 會被 pytest 判成 INTERNALERROR，
    但 `os._exit(0)` 直接結束行程、pytest 來不及回報，結束碼就是 0。最後一個
    更進一步偽造 stdout 的通過標記——判準已移到 pytest 外掛寫出的結果檔
    （見 executor._verdict_ok），故一併在這裡擋住：五個這種答案曾能湊出
    「已掌握」。
    """
    client, did, cid = ctx
    forged = ("import os\n"
              "for fd in range(3, 40):\n"
              "    try: os.write(fd, b'1 passed in 0.01s\\n')\n"
              "    except OSError: pass\n"
              "os._exit(0)\n")
    for answer in ("import sys\nsys.exit(0)", "import os\nos._exit(0)", "raise SystemExit(0)", forged):
        qid = make_question(client, cid, monkeypatch, "write",
                            {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": answer})
        assert r.get_json()["attempt"]["verdict"] == "wrong", answer


def test_write_question_without_test_code_is_rejected(ctx, monkeypatch):
    """題目可能在不可執行時生成（驗證被跳過），之後才被翻成可執行。

    這種題目沒有任何判準，不能因為答案能執行完就判 correct。
    """
    client, did, cid = ctx
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": ""}, CORRECT_ANSWER)
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "print('anything')"})
    assert r.status_code == 422
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []


def test_principle_answer_is_graded_against_the_real_output(ctx, monkeypatch):
    """學習者的預測要對照程式**真的**印出什麼，不是對照模型聲稱的標準答案。

    這裡刻意不替換 grade_answer，讓批改提示詞真的被組出來，才能斷言真實輸出
    進得了提示詞——模型說「輸出 3」而程式實際印 2 時，批改依據必須是 2。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "principle",
                        {"code_snippet": "print(1 + 1)"}, "3")
    prompts = _capture_grade_prompts(monkeypatch)
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "2"})
    assert r.get_json()["attempt"]["verdict"] == "correct"
    assert "標準答案：3" in prompts[0]                       # 模型聲稱的輸出仍在
    assert "取得的真實輸出（這是事實，不是參考答案）：\n2\n" in prompts[0]


def test_principle_answer_without_executable_falls_back_to_claude(ctx, monkeypatch):
    """非程式領域（executable=0）沒有執行器可用，一律回 Claude 批改。"""
    client, did, cid = ctx
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "principle",
                        {"code_snippet": "print(1 / 0)"}, "會拋錯")

    def boom(*a, **k):
        raise AssertionError("不可執行領域不該執行學習者的題目程式碼")

    monkeypatch.setattr(app_module, "run_python", boom)
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "ok", "root_cause": None})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "會拋錯"})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_principle_answer_falls_back_to_stderr_when_nothing_was_printed(ctx, monkeypatch):
    """程式什麼都沒印（拋例外）時，traceback 本身就是可對照的真實輸出。

    少了這條 fallback，「預測會拋出什麼例外」的題目會變成對著空字串批改。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "principle",
                        {"code_snippet": "print(1 / 0)"}, "會拋錯")
    prompts = _capture_grade_prompts(monkeypatch)
    client.post(f"/api/questions/{qid}/answer", json={"answer": "ZeroDivisionError"})
    assert "ZeroDivisionError" in prompts[0]


def test_principle_question_without_snippet_is_rejected_at_answer_time(ctx, monkeypatch):
    """題目可能是「不可執行」時生成的，之後才被 override 翻成可執行。

    這種題目連程式碼都沒有，無從錨定——比照 write 題缺 test_code 的處理，
    回 422 且不留 attempt。
    """
    client, did, cid = ctx
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "principle", {"code_snippet": ""}, "1")
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    # 哨兵：真的走到批改就會擲出（而不是打真 API），錯誤的路徑因此會回 500
    monkeypatch.setattr(tutor, "grade_answer",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該進到批改")))
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "1"})
    assert r.status_code == 422
    conn = db.connect(client.application.config["DB_PATH"])
    assert db.list_attempts_for_concept(conn, cid) == []
    conn.close()


def test_principle_env_failure_is_not_counted_as_wrong(ctx, monkeypatch):
    """執行環境失敗與 write 題同一條規則：不是「答錯」，不落 attempt。"""
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "principle", {"code_snippet": "print(1)"}, "1")
    monkeypatch.setattr(app_module, "run_python",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "執行環境錯誤：x",
                                         "timed_out": False, "env_error": True})
    # 哨兵：真的走到批改就會擲出（而不是打真 API），錯誤的路徑因此會回 500
    monkeypatch.setattr(tutor, "grade_answer",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該進到批改")))
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "1"})
    assert r.status_code == 503
    conn = db.connect(client.application.config["DB_PATH"])
    assert db.list_attempts_for_concept(conn, cid) == []
    conn.close()


def test_principle_timeout_is_not_counted_as_wrong(ctx, monkeypatch):
    """逾時是「沒跑完」不是「答錯」，判成 wrong 會污染掌握度。

    題目先以正常片段建立（建立時也要真的跑得動），再把執行器換成逾時。
    """
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "principle", {"code_snippet": "print(1)"}, "1")
    monkeypatch.setattr(app_module, "run_python",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "",
                                         "timed_out": True, "env_error": False})
    # 哨兵：真的走到批改就會擲出（而不是打真 API），錯誤的路徑因此會回 500
    monkeypatch.setattr(tutor, "grade_answer",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該進到批改")))
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "1"})
    assert r.status_code == 408
    conn = db.connect(client.application.config["DB_PATH"])
    assert db.list_attempts_for_concept(conn, cid) == []
    conn.close()


def test_exam_answer_graded_by_claude(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "閉包會捕捉定義環境的變數")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "partial", "feedback": "少講了 late binding", "root_cause": "未掌握 late binding"})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "就是函式包變數"})
    body = r.get_json()
    assert body["attempt"]["verdict"] == "partial"
    assert body["attempt"]["root_cause"] == "未掌握 late binding"


def test_mastery_updates_after_answers(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "好", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_four_of_five_is_mastered(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    verdicts = iter(["correct", "correct", "correct", "correct", "wrong"])
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": next(verdicts), "feedback": "f", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_timeout_is_not_counted_as_wrong(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    # 逾時代表「沒跑完」而非「寫錯」。判成 wrong 會污染掌握度，故必須 408
    # 且不留 attempt。（真正的 5 秒逾時由 test_executor.py 覆蓋，這裡只驗端點。）
    monkeypatch.setattr(app_module, "run_pytest",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "",
                                         "timed_out": True, "env_error": False})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "while True: pass"})
    assert r.status_code == 408
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["status"] == "untested"


def test_env_failure_is_not_counted_as_wrong(ctx, monkeypatch):
    # 執行環境失敗同樣不是「寫錯」（executor 以 env_error 區分），不得落 attempt
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    monkeypatch.setattr(app_module, "run_pytest",
                        lambda *a, **k: {"ok": False, "stdout": "", "stderr": "執行環境錯誤：x",
                                         "timed_out": False, "env_error": True})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.status_code == 503
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert attempts == []
    assert client.get(f"/api/domains/{did}").get_json()["concepts"][0]["status"] == "untested"


def test_write_question_without_executable_falls_back_to_claude(ctx, monkeypatch):
    client, did, cid = ctx
    # 領域不可執行時，就算題型是 write 也沒有客觀判準，只能交給 Agent 批改
    client.post(f"/api/domains/{did}/override_executable", json={"executable": False})
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": TEST_CODE}, CORRECT_ANSWER)
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "ok", "root_cause": None})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "純文字作答"})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_attempt_is_persisted(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "wrong", "feedback": "f", "root_cause": "rc"})
    client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert len(attempts) == 1
    assert attempts[0]["verdict"] == "wrong"
