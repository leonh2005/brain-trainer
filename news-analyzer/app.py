import json
import os
import sys
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import DB_PATH as _DEFAULT_DB, get_articles, get_trend_data, get_conn, init_db

app = Flask(__name__)
DB_PATH = _DEFAULT_DB


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/articles")
def api_articles():
    source = request.args.get("source")
    date = request.args.get("date")
    score_min = request.args.get("score_min", type=int)
    score_max = request.args.get("score_max", type=int)
    query = request.args.get("q")
    page = request.args.get("page", 1, type=int)
    result = get_articles(
        source=source, date=date,
        score_min=score_min, score_max=score_max,
        query=query, page=page,
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
    rows = get_trend_data(period=period, db_path=DB_PATH)

    labels_set = sorted({r["t"] for r in rows})
    sources = sorted({r["source"] for r in rows})
    score_map = {(r["source"], r["t"]): r["avg_score"] for r in rows}

    COLORS = {
        "cnyes": "#3b82f6", "yahoo": "#8b5cf6",
        "ptt": "#f59e0b", "reddit": "#ef4444", "x": "#10b981",
    }

    datasets = []
    for src in sources:
        datasets.append({
            "label": src,
            "data": [score_map.get((src, t)) for t in labels_set],
            "borderColor": COLORS.get(src, "#6b7280"),
            "backgroundColor": COLORS.get(src, "#6b7280") + "33",
            "tension": 0.3,
            "spanGaps": True,
        })

    return jsonify({"labels": labels_set, "datasets": datasets})


@app.route("/api/stats")
def api_stats():
    date = request.args.get("date")
    condition = "DATE(fetched_at) = ?" if date else "fetched_at >= datetime('now', '-24 hours')"
    params = [date] if date else []
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT score FROM articles WHERE score IS NOT NULL AND {condition}",
            params,
        ).fetchall()
    total = len(rows)
    if total == 0:
        return jsonify({"total": 0, "bullish": 0, "bearish": 0, "neutral": 0,
                        "bullish_pct": 0, "bearish_pct": 0, "neutral_pct": 0})
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
    })


if __name__ == "__main__":
    init_db(DB_PATH)
    app.run(host="0.0.0.0", port=5300, debug=False)
