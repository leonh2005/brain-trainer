import threading

from flask import Flask, jsonify, render_template, request

from learn_system import db, tutor

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
    conn = db.connect(_DB_PATH)
    try:
        domain = db.get_domain(conn, domain_id)
        try:
            result = tutor.generate_map(domain["name"], domain["goals"], bool(domain["verify_sources"]))
        except tutor.TutorError:
            db.set_domain_status(conn, domain_id, "failed")
            return
        concepts = result["concepts"]
        if not concepts:
            db.set_domain_status(conn, domain_id, "failed")
            return
        db.set_domain_executable(conn, domain_id, result["executable"])
        for c in concepts:
            db.create_concept(conn, domain_id, c["name"], c["section"], c.get("description"))
        db.set_domain_status(conn, domain_id, "ready")
    finally:
        conn.close()


def _spawn_map_generation(app, domain_id):
    threading.Thread(target=_generate_map, args=(domain_id,), daemon=True).start()


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

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5990, debug=False)
