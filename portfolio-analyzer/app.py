from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
load_dotenv()
import json
from pathlib import Path
from analysis import get_portfolio_data, PORTFOLIO_FILE
from ai_masters import get_all_analyses
from dcf import get_dcf_data

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


@app.route("/api/positions", methods=["POST"])
def save_positions():
    try:
        positions = request.json
        if not isinstance(positions, list):
            return jsonify({"ok": False, "error": "格式錯誤"}), 400
        with open(PORTFOLIO_FILE) as f:
            portfolio = json.load(f)
        taiwan, us = [], []
        for p in positions:
            ticker = p.get("ticker", "").strip()
            if not ticker:
                continue
            entry = {"ticker": ticker, "name": p.get("name", ""), "shares": int(p.get("shares", 0))}
            if ticker.endswith(".TW") or ticker.endswith(".TWO"):
                taiwan.append(entry)
            else:
                us.append(entry)
        portfolio["taiwan"] = taiwan
        portfolio["us"] = us
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/targets", methods=["POST"])
def save_targets():
    try:
        targets = request.json
        if not isinstance(targets, list):
            return jsonify({"ok": False, "error": "格式錯誤"}), 400
        with open(PORTFOLIO_FILE) as f:
            portfolio = json.load(f)
        portfolio["target"] = targets
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dcf")
def api_dcf():
    try:
        force = request.args.get("refresh") == "1"
        portfolio_data = get_portfolio_data()
        results = get_dcf_data(portfolio_data["positions"], force_refresh=force)
        return jsonify({"ok": True, "data": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5800, debug=False)
