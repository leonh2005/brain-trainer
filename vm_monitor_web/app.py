"""Mac 端 VM 資源檢視網頁（127.0.0.1:5982）。"""

from flask import Flask, jsonify, render_template

from .stats import VM_HOST, collect

app = Flask(__name__)
INTERVAL_MS = 5000


@app.get("/")
def index() -> str:
    return render_template("index.html", interval_ms=INTERVAL_MS, host=VM_HOST)


@app.get("/api/stats")
def api_stats():
    try:
        data = collect()
    except Exception as exc:
        response = jsonify({"ok": False, "error": str(exc)})
    else:
        response = jsonify({"ok": True, **data})
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5982)
