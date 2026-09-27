import ast
import logging
import threading

from flask import Flask, jsonify, render_template, request

from learn_system import db, mastery, tutor
from learn_system.executor import run_pytest, run_python

logger = logging.getLogger(__name__)

# 錯題本一次回傳的上限。attempts 只增不減，不設上限查詢成本會隨練習量成長。
MISTAKES_LIMIT = 50

# create_app 指定的資料庫路徑。背景執行緒不在 app context 內，且測試替身以
# 單一參數呼叫 _generate_map，故以模組層變數記住，None 代表用 config.DB_PATH。
_DB_PATH = None


def _mastery_pct(conn, domain_id):
    concepts = db.list_concepts(conn, domain_id)
    if not concepts:
        return 0
    mastered = sum(1 for c in concepts if c["status"] == "mastered")
    return round(mastered * 100 / len(concepts))


def _generate_map(domain_id):
    """背景生成智識地圖。任何失敗都必須讓狀態停在 failed。

    這條執行緒一旦讓例外逃出去就會直接死亡，資料列將永遠停在 schema 預設的
    generating，前端會無止境地輪詢且沒有錯誤狀態可顯示，故除了可預期的
    TutorError 之外，任何非預期例外（Agent 回傳非物件 JSON、concepts 非序列、
    sqlite 錯誤等）也一律標記 failed。
    """
    conn = db.connect(_DB_PATH)
    try:
        try:
            domain = db.get_domain(conn, domain_id)
            if domain is None:
                return
            result = tutor.generate_map(domain["name"], domain["goals"], bool(domain["verify_sources"]))
            concepts = result["concepts"]
            if not concepts:
                raise tutor.TutorError("Agent 未產出任何概念")
            verify = bool(domain["verify_sources"])
            db.set_domain_executable(conn, domain_id, result["executable"])
            for c in concepts:
                # source_urls 只在查證模式下入庫（見 tutor.generate_map 的清理）
                db.create_concept(conn, domain_id, c["name"], c["section"], c.get("description"),
                                  source_urls=c.get("source_urls") if verify else None)
        except tutor.TutorError as e:
            logger.warning("智識地圖生成失敗：domain %s：%s", domain_id, e)
            db.set_domain_status(conn, domain_id, "failed")
        except Exception:
            logger.exception("智識地圖生成非預期失敗：domain %s", domain_id)
            db.set_domain_status(conn, domain_id, "failed")
        else:
            db.set_domain_status(conn, domain_id, "ready")
    finally:
        conn.close()


def _spawn_map_generation(app, domain_id):
    threading.Thread(target=_generate_map, args=(domain_id,), daemon=True).start()


def _validate_question(q, goal_type):
    """確認題目本身有效：write 題的參考解要能過測試，
    read 題的原版與修正版行為必須不同，且修正版真的通過，
    principle 題的程式碼必須真的跑得動（跑不動就沒有可對照的真實輸出）。

    read 題只比對「原版是否拋錯」是不夠的——bug 若是「輸出錯誤值」，
    原版仍會 ok=True，會被誤判成無效題，故改以行為差異為判準。
    """
    payload = q["payload"]
    if goal_type == "write":
        code = q.get("reference_answer") or payload.get("starter_code") or ""
        test_code = payload.get("test_code", "")
        if not code.strip() or not test_code.strip():
            return False
        # 參考解必須真的讓測試通過。用 run_pytest 而非 run_python：test_code
        # 當純腳本跑時 `def test_x(): ...` 從不被呼叫，任何參考解都會「通過」，
        # 這種永遠不會失敗的測試等於沒有測試。
        return run_pytest(code, test_code)["ok"]

    if goal_type == "read":
        original = run_python(payload["code_snippet"])
        fixed = run_python(payload["fixed_code"])
        differs = (original["stdout"] != fixed["stdout"]) or (original["ok"] != fixed["ok"])
        return differs and fixed["ok"]

    if goal_type == "principle":
        snippet = payload.get("code_snippet", "")
        if not snippet.strip():
            return False
        # 「跑不動」與「跑起來之後拋例外」是兩件事，不能混為一談：
        # - 解析不了（語法錯誤）＝模型給的程式碼本身是壞的，學習者無從預測 → 作廢
        # - 執行逾時／環境失敗＝拿不到輸出，同樣沒有錨點 → 作廢
        # - 跑起來但拋例外＝**是有效的題**（「這段程式會丟出什麼例外」），真實輸出
        #   就是 stderr 的 traceback，批改端以 `stdout or stderr` 取用
        #   （見 answer_question）。若在這裡要求結束碼為 0，這一類題目會永遠
        #   出不出來——與批改端的行為互相矛盾。
        try:
            ast.parse(snippet)
        except (SyntaxError, ValueError):
            return False
        result = run_python(snippet)
        return not result["timed_out"] and not result["env_error"]

    return True


def create_app(db_path=None):
    global _DB_PATH
    _DB_PATH = db_path

    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.close()

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/domain/<int:domain_id>")
    def domain_page(domain_id):
        return render_template("domain.html", domain_id=domain_id)

    @app.get("/api/domains")
    def list_domains():
        conn = db.connect(db_path)
        try:
            out = []
            for d in db.list_domains(conn):
                out.append({
                    "id": d["id"],
                    "name": d["name"],
                    "goals": d["goals"],
                    "status": d["status"],
                    "mastery_pct": _mastery_pct(conn, d["id"]),
                    "concept_count": len(db.list_concepts(conn, d["id"])),
                    "created_at": d["created_at"],
                })
            return jsonify(domains=out)
        finally:
            conn.close()

    @app.post("/api/domains")
    def create_domain():
        body = request.get_json(force=True)
        name = (body.get("name") or "").strip()
        goals = body.get("goals") or []
        if not name:
            return jsonify(error="領域名稱不可為空"), 400
        if not goals:
            return jsonify(error="至少要勾選一個學習目標"), 400
        conn = db.connect(db_path)
        try:
            domain_id = db.create_domain(conn, name, goals, bool(body.get("verify_sources")))
        finally:
            conn.close()
        _spawn_map_generation(app, domain_id)
        return jsonify(id=domain_id)

    @app.get("/api/domains/<int:domain_id>")
    def get_domain(domain_id):
        conn = db.connect(db_path)
        try:
            domain = db.get_domain(conn, domain_id)
            if not domain:
                return jsonify(error="找不到領域"), 404
            concepts = db.list_concepts(conn, domain_id)
            return jsonify(domain=domain, concepts=concepts, progress={
                "mastery_pct": _mastery_pct(conn, domain_id),
                "counts": {
                    "untested": sum(1 for c in concepts if c["status"] == "untested"),
                    "practicing": sum(1 for c in concepts if c["status"] == "practicing"),
                    "mastered": sum(1 for c in concepts if c["status"] == "mastered"),
                },
            })
        finally:
            conn.close()

    @app.delete("/api/domains/<int:domain_id>")
    def delete_domain(domain_id):
        """刪除領域。子資料（概念／題目／作答）由 schema 的 cascade 連帶刪除，
        不另外清除；先確認存在才刪，未知的 id 一律 404。"""
        conn = db.connect(db_path)
        try:
            if db.get_domain(conn, domain_id) is None:
                return jsonify(error="找不到領域"), 404
            db.delete_domain(conn, domain_id)
        finally:
            conn.close()
        return jsonify(ok=True)

    @app.get("/api/domains/<int:domain_id>/mistakes")
    def list_mistakes(domain_id):
        """錯題本：這個領域所有非 correct 的作答，新的在前。

        查詢帶上限（MISTAKES_LIMIT）後才在 Python 端濾掉 correct；先撈全部再
        過濾會讓這個只增不減的資料表拖慢每次載入。
        """
        conn = db.connect(db_path)
        try:
            if db.get_domain(conn, domain_id) is None:
                return jsonify(error="找不到領域"), 404
            attempts = db.list_attempts_for_domain(conn, domain_id, limit=MISTAKES_LIMIT)
            return jsonify(mistakes=[a for a in attempts if a["verdict"] != "correct"])
        finally:
            conn.close()

    @app.post("/api/domains/<int:domain_id>/regenerate")
    def regenerate_domain(domain_id):
        """重新生成智識地圖——failed 領域的復原路徑（spec §5.2 的重試鈕）。

        先清掉既有概念再重跑：中斷留下的半套概念若留著，新的地圖會疊在舊的
        上面。對 ready 的領域也可安全呼叫，語意就是「這張地圖重生成一次」；
        它的概念會被換成新的一份（舊概念與其作答一併消失，故前端只在 failed
        時提供按鈕）。生成中的領域一律拒絕：再起一條執行緒會讓兩邊同時寫入
        概念（疊成兩份），而且舊的那條最後還會把狀態寫回 ready，蓋掉新的
        generating——留下的是一個沒有任何東西在跑的「生成中」。
        """
        conn = db.connect(db_path)
        try:
            domain = db.get_domain(conn, domain_id)
            if domain is None:
                return jsonify(error="找不到領域"), 404
            if domain["status"] == "generating":
                return jsonify(error="智識地圖正在生成中，請稍候"), 409
            db.delete_concepts(conn, domain_id)
            db.set_domain_status(conn, domain_id, "generating")
        finally:
            conn.close()
        _spawn_map_generation(app, domain_id)
        return jsonify(ok=True)

    @app.post("/api/domains/<int:domain_id>/override_executable")
    def override_executable(domain_id):
        body = request.get_json(force=True)
        conn = db.connect(db_path)
        try:
            db.set_domain_executable(conn, domain_id, body.get("executable", True))
        finally:
            conn.close()
        return jsonify(ok=True)

    @app.post("/api/concepts/<int:concept_id>/questions")
    def create_question(concept_id):
        body = request.get_json(force=True)
        goal_type = body.get("goal_type")
        if goal_type not in ("write", "read", "principle", "exam"):
            return jsonify(error="未知的題型"), 400

        conn = db.connect(db_path)
        try:
            concept = db.get_concept(conn, concept_id)
            if not concept:
                return jsonify(error="找不到概念"), 404
            domain = db.get_domain(conn, concept["domain_id"])
            executable = bool(domain["executable"])

            try:
                q = tutor.generate_question(domain["name"], concept["name"],
                                            concept["description"], goal_type, executable)
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502

            if executable and not _validate_question(q, goal_type):
                return jsonify(error="題目無效：執行驗證未通過，請重試"), 422

            qid = db.create_question(conn, concept_id, goal_type, q["prompt"], q["payload"], q["reference_answer"])
            saved = db.get_question(conn, qid)
            return jsonify(question=saved)
        finally:
            conn.close()

    @app.post("/api/questions/<int:question_id>/answer")
    def answer_question(question_id):
        body = request.get_json(force=True)
        answer = body.get("answer", "")
        conn = db.connect(db_path)
        try:
            question = db.get_question(conn, question_id)
            if not question:
                return jsonify(error="找不到題目"), 404
            concept = db.get_concept(conn, question["concept_id"])
            domain = db.get_domain(conn, concept["domain_id"])
            executable = bool(domain["executable"])
            goal_type = question["goal_type"]

            # write 題有客觀判準（測試跑不跑得過），直接執行答案對測試，
            # 不經過模型；其餘題型才交由 Agent 批改。
            if goal_type == "write" and executable:
                test_code = question["payload"].get("test_code", "")
                # 題目可能是在「不可執行」時生成（驗證被跳過），之後才用
                # override_executable 翻成可執行；這種題目沒有任何判準。
                if not test_code.strip():
                    return jsonify(error="題目無效：缺少 test_code，請重新出題"), 422
                result = run_pytest(answer, test_code)
                # 逾時與執行環境失敗都是「沒跑完」而非「寫錯」，判成 wrong 會
                # 污染掌握度，故回報錯誤且不留下 attempt。
                if result["timed_out"]:
                    return jsonify(error="執行逾時（超過 5 秒），不計入對錯，請修改後重試"), 408
                if result["env_error"]:
                    return jsonify(error="執行環境錯誤，不計入對錯，請稍後重試"), 503
                verdict = "correct" if result["ok"] else "wrong"
                # pytest 把失敗報告寫在 stdout，stderr 只在極少數情況有內容（實測是
                # 學習者的程式碼觸發 SystemExit 時的一行 mainloop 訊息）。兩者都有
                # 內容時必須以 stdout 為準，否則學習者只看得到那行雜訊而不是報告。
                detail = result["stdout"] or result["stderr"]
                feedback = "測試通過" if result["ok"] else f"測試失敗：\n{detail[-800:]}"
                root_cause = None if result["ok"] else "程式未通過測試"
            else:
                # principle 題同樣有客觀錨點：真實輸出由執行取得，不由模型聲稱。
                # 沒有這一步，學習者的預測是對照模型「以為」的輸出，模型講錯就
                # 判錯人（read/write 已有各自的執行錨點，principle 是唯一缺的）。
                observed = None
                if goal_type == "principle" and executable:
                    snippet = question["payload"].get("code_snippet", "")
                    # 題目可能是「不可執行」時生成、之後才被翻成可執行的，這種題目
                    # 連程式碼都沒有，無從錨定。
                    if not snippet.strip():
                        return jsonify(error="題目無效：缺少 code_snippet，請重新出題"), 422
                    result = run_python(snippet)
                    # 與 write 題同一條規則：逾時／執行環境失敗都是「沒跑完」而非
                    # 「答錯」，判成 wrong 會污染掌握度，故回報錯誤且不留 attempt。
                    if result["timed_out"]:
                        return jsonify(error="執行逾時（超過 5 秒），不計入對錯，請稍後重試"), 408
                    if result["env_error"]:
                        return jsonify(error="執行環境錯誤，不計入對錯，請稍後重試"), 503
                    # stdout 為空時（題目是「會拋出什麼例外」這類）錯誤訊息本身就是
                    # 真實輸出，與 write 題顯示報告的取捨同一個道理。
                    # 只留尾端 800 字：與 write 題的失敗報告同一個上限，程式碼是
                    # 模型給的，不能假設它不會噴出上萬行把 prompt 灌爆。
                    observed = (result["stdout"] or result["stderr"])[-800:]
                try:
                    graded = tutor.grade_answer(domain["name"], concept["name"], question, answer,
                                                executable, observed_output=observed)
                except tutor.TutorError as e:
                    return jsonify(error=str(e)), 502
                verdict, feedback, root_cause = graded["verdict"], graded["feedback"], graded["root_cause"]

            db.create_attempt(conn, question_id, answer, verdict, feedback, root_cause)
            status = mastery.compute_status(db.list_attempts_for_concept(conn, concept["id"]))
            db.set_concept_status(conn, concept["id"], status)
            attempts = db.list_attempts_for_concept(conn, concept["id"], limit=1)
            return jsonify(attempt=attempts[0], concept_status=status)
        finally:
            conn.close()

    @app.post("/api/concepts/<int:concept_id>/explain")
    def explain(concept_id):
        conn = db.connect(db_path)
        try:
            concept = db.get_concept(conn, concept_id)
            if not concept:
                return jsonify(error="找不到概念"), 404
            if concept["description"]:
                return jsonify(description=concept["description"])
            domain = db.get_domain(conn, concept["domain_id"])
            try:
                text = tutor.explain_concept(domain["name"], concept["name"],
                                             concept["section"], bool(domain["verify_sources"]))
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502
            db.set_concept_description(conn, concept_id, text)
            return jsonify(description=text)
        finally:
            conn.close()

    @app.post("/api/domains/<int:domain_id>/chat")
    def chat(domain_id):
        body = request.get_json(force=True)
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify(error="訊息不可為空"), 400
        conn = db.connect(db_path)
        try:
            domain = db.get_domain(conn, domain_id)
            if not domain:
                return jsonify(error="找不到領域"), 404
            concept_name = None
            if body.get("concept_id"):
                concept = db.get_concept(conn, body["concept_id"])
                concept_name = concept["name"] if concept else None
            try:
                reply, new_session = tutor.chat(domain["name"], concept_name, message, domain["chat_session_id"])
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502
            if new_session and new_session != domain["chat_session_id"]:
                db.set_domain_chat_session(conn, domain_id, new_session)
            return jsonify(reply=reply)
        finally:
            conn.close()

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5990, debug=False)
