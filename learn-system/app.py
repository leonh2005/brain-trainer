import logging
import threading

from flask import Flask, jsonify, render_template, request

from learn_system import db, mastery, tutor
from learn_system.executor import run_python

logger = logging.getLogger(__name__)

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
            db.set_domain_executable(conn, domain_id, result["executable"])
            for c in concepts:
                db.create_concept(conn, domain_id, c["name"], c["section"], c.get("description"))
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
    read 題的原版與修正版行為必須不同，且修正版真的通過。

    read 題只比對「原版是否拋錯」是不夠的——bug 若是「輸出錯誤值」，
    原版仍會 ok=True，會被誤判成無效題，故改以行為差異為判準。
    """
    payload = q["payload"]
    if goal_type == "write":
        code = q.get("reference_answer") or payload.get("starter_code") or ""
        if not code.strip():
            return False
        combined = code + "\n\n" + payload.get("test_code", "")
        return run_python(combined)["ok"]

    if goal_type == "read":
        original = run_python(payload["code_snippet"])
        fixed = run_python(payload["fixed_code"])
        differs = (original["stdout"] != fixed["stdout"]) or (original["ok"] != fixed["ok"])
        return differs and fixed["ok"]

    if goal_type == "principle":
        return bool(payload.get("code_snippet", "").strip())

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

            # write 題有客觀判準（測試跑不跑得過），直接執行答案加測試，
            # 不經過模型；其餘題型才交由 Agent 批改。
            if goal_type == "write" and executable:
                result = run_python(answer + "\n\n" + question["payload"].get("test_code", ""))
                # 逾時是「沒跑完」而非「寫錯」，判成 wrong 會污染掌握度，
                # 故以 408 回報且不留下 attempt。
                if result["timed_out"]:
                    return jsonify(error="執行逾時（超過 5 秒），不計入對錯，請修改後重試"), 408
                verdict = "correct" if result["ok"] else "wrong"
                feedback = "測試通過" if result["ok"] else f"測試失敗：\n{result['stderr'][-800:]}"
                root_cause = None if result["ok"] else "程式未通過測試"
            else:
                try:
                    graded = tutor.grade_answer(domain["name"], concept["name"], question, answer, executable)
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

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5990, debug=False)
