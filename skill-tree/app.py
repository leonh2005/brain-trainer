from flask import Flask, render_template, request, redirect, url_for, jsonify
from db import init_db, get_all_categories, get_skills_by_category, get_skill_detail, log_xp, get_character

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html",
                           categories=get_all_categories(),
                           character=get_character())

@app.route("/category/<int:cat_id>")
def category(cat_id):
    cats = get_all_categories()
    cat = next((c for c in cats if c["id"] == cat_id), None)
    if not cat:
        return "Not found", 404
    return render_template("category.html",
                           category=cat,
                           skills=get_skills_by_category(cat_id))

@app.route("/skill/<int:skill_id>")
def skill(skill_id):
    detail = get_skill_detail(skill_id)
    if not detail:
        return "Not found", 404
    return render_template("skill.html", skill=detail)

@app.route("/skill/<int:skill_id>/log", methods=["POST"])
def log_skill_xp(skill_id):
    hours = request.form.get("hours", "")
    xp_direct = request.form.get("xp", "")
    note = request.form.get("note", "")
    if xp_direct:
        xp = int(xp_direct)
    elif hours:
        xp = int(float(hours) * 60)
    else:
        xp = 0
    if xp > 0:
        log_xp(skill_id, xp, note)
    return redirect(url_for("skill", skill_id=skill_id))

@app.route("/api/character")
def api_character():
    return jsonify(get_character())

@app.route("/api/categories")
def api_categories():
    return jsonify(get_all_categories())

if __name__ == "__main__":
    init_db()
    app.run(port=5500, debug=True)
