import json
import time
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from mapping import ACTIONS, BUTTONS, PRESETS, load_config, save_config

STATE_PATH = Path(__file__).parent / "state.json"
STALE_SEC = 6

app = Flask(__name__)


@app.route("/")
def index():
    config = load_config()
    rows = [
        (button_id, label, config.get(button_id, "none"))
        for button_id, label in BUTTONS
    ]
    action_options = [(aid, a["label"]) for aid, a in ACTIONS.items()]
    saved = request.args.get("saved") == "1"
    return render_template("index.html", rows=rows, action_options=action_options, saved=saved)


@app.route("/api/config", methods=["POST"])
def update_config():
    config = load_config()
    for button_id, _ in BUTTONS:
        value = request.form.get(button_id)
        if value in ACTIONS:
            config[button_id] = value
    save_config(config)
    return redirect(url_for("index", saved=1))


@app.route("/api/preset/<name>", methods=["POST"])
def apply_preset(name):
    if name in PRESETS:
        save_config(dict(PRESETS[name]))
    return redirect(url_for("index", saved=1))


@app.route("/api/status")
def status():
    if not STATE_PATH.exists():
        return jsonify({"connected": False, "controller_name": "", "last_action": None})
    try:
        data = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return jsonify({"connected": False, "controller_name": "", "last_action": None})
    if time.time() - data.get("updated_at", 0) > STALE_SEC:
        data["connected"] = False
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5970)
