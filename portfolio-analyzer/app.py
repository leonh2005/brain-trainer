from flask import Flask, render_template, jsonify, request
from analysis import get_portfolio_data
from ai_masters import get_all_analyses

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    try:
        return jsonify({"ok": True, "data": get_portfolio_data()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh")
def api_refresh():
    try:
        return jsonify({"ok": True, "data": get_portfolio_data(force_refresh=True)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ai-analysis")
def api_ai():
    try:
        force = request.args.get("refresh") == "1"
        portfolio_data = get_portfolio_data()
        results = get_all_analyses(portfolio_data, force_refresh=force)
        return jsonify({"ok": True, "data": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5800, debug=False)
