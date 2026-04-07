import json
import os
import sys
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import DB_PATH as _DEFAULT_DB, get_articles, get_trend_data, get_conn, init_db, set_irrelevant

app = Flask(__name__)
DB_PATH = _DEFAULT_DB


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/articles/<int:article_id>/irrelevant", methods=["POST"])
def api_set_irrelevant(article_id):
    value = request.json.get("irrelevant", True)
    set_irrelevant(article_id, value, DB_PATH)
    return jsonify({"ok": True})


@app.route("/api/articles")
def api_articles():
    source = request.args.get("source")
    date = request.args.get("date")
    score_min = request.args.get("score_min", type=int)
    score_max = request.args.get("score_max", type=int)
    query = request.args.get("q")
    page = request.args.get("page", 1, type=int)
    show_irrelevant = request.args.get("irrelevant") == "1"
    result = get_articles(
        source=source, date=date,
        score_min=score_min, score_max=score_max,
        query=query, page=page,
        show_irrelevant=show_irrelevant,
        db_path=DB_PATH,
    )
    for a in result["articles"]:
        if a.get("tags"):
            try:
                a["tags"] = json.loads(a["tags"])
            except Exception:
                a["tags"] = []
    return jsonify(result)


@app.route("/api/trend")
def api_trend():
    period = request.args.get("period", "day")
    rows, all_labels = get_trend_data(period=period, db_path=DB_PATH)

    sources = sorted({r["source"] for r in rows})
    score_map = {(r["source"], r["t"]): r["avg_score"] for r in rows}

    COLORS = {
        "cnyes":       "#e53e3e",
        "yahoo":       "#7c3aed",
        "yahoo_tw":    "#7c3aed",
        "yahoo_us":    "#4f46e5",
        "udn":         "#0369a1",
        "marketwatch": "#0f766e",
        "cnbc":        "#d97706",
        "ptt":         "#15803d",
        "reddit":      "#c2410c",
        "x":           "#1d4ed8",
    }

    datasets = []
    for src in sources:
        datasets.append({
            "label": src,
            "data": [score_map.get((src, t)) for t in all_labels],
            "borderColor": COLORS.get(src, "#6b7280"),
            "backgroundColor": COLORS.get(src, "#6b7280"),
            "pointStyle": "rect",
            "tension": 0.3,
            "spanGaps": True,
        })

    return jsonify({"labels": all_labels, "datasets": datasets})


@app.route("/api/stats")
def api_stats():
    date = request.args.get("date")
    period = request.args.get("period", "day")
    if date:
        condition = "DATE(fetched_at) = ?"
        params = [date]
    elif period == "week":
        condition = "fetched_at >= datetime('now', '-7 days')"
        params = []
    elif period == "month":
        condition = "fetched_at >= datetime('now', '-30 days')"
        params = []
    else:
        condition = "fetched_at >= datetime('now', '-24 hours')"
        params = []
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT score FROM articles WHERE score IS NOT NULL AND irrelevant = 0 AND {condition}",
            params,
        ).fetchall()
        last_row = conn.execute(
            "SELECT fetched_at FROM articles ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
    last_updated = last_row["fetched_at"][:16].replace("T", " ") if last_row else None
    total = len(rows)
    if total == 0:
        return jsonify({"total": 0, "bullish": 0, "bearish": 0, "neutral": 0,
                        "bullish_pct": 0, "bearish_pct": 0, "neutral_pct": 0,
                        "last_updated": last_updated})
    bullish = sum(1 for r in rows if r["score"] >= 7)
    bearish = sum(1 for r in rows if r["score"] <= 4)
    neutral = total - bullish - bearish
    return jsonify({
        "total": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "bullish_pct": round(bullish / total * 100, 1),
        "bearish_pct": round(bearish / total * 100, 1),
        "neutral_pct": round(neutral / total * 100, 1),
        "last_updated": last_updated,
    })


if __name__ == "__main__":
    init_db(DB_PATH)
    app.run(host="0.0.0.0", port=5300, debug=False)
