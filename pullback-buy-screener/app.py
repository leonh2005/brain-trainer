# -*- coding: utf-8 -*-
"""回後買上漲選股（port 5960）：一鍵掃描全市場，找符合「多頭回檔後長紅突破」的標的。"""

import threading
import time

from flask import Flask, jsonify, render_template, request

import screener

app = Flask(__name__)

_lock = threading.Lock()
_job = {"id": 0, "status": "idle", "done": 0, "total": 0, "results": []}


def _run_scan(job_id: int, n_universe: int) -> None:
    def progress_cb(done, total):
        with _lock:
            if _job["id"] == job_id:
                _job["done"] = done
                _job["total"] = total

    try:
        results = screener.scan(n_universe, progress_cb=progress_cb)
        with _lock:
            if _job["id"] == job_id:
                _job["status"] = "done"
                _job["results"] = results
    except Exception as e:
        with _lock:
            if _job["id"] == job_id:
                _job["status"] = "error"
                _job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    n_universe = min(600, int((request.get_json(silent=True) or {}).get("universe", 200)))
    with _lock:
        if _job["status"] == "running":
            return jsonify({"job_id": _job["id"]})
        _job["id"] += 1
        _job.update(status="running", done=0, total=n_universe, results=[])
        job_id = _job["id"]
    threading.Thread(target=_run_scan, args=(job_id, n_universe), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/scan/status")
def scan_status():
    with _lock:
        return jsonify({
            "status": _job["status"],
            "done": _job["done"],
            "total": _job["total"],
            "results": _job["results"] if _job["status"] == "done" else [],
            "error": _job.get("error"),
        })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5960)
