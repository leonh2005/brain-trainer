#!/usr/bin/env python3
"""Steven's Dashboard — 服務監控中心 (port 5600)"""

import os
import re
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify
import requests

app = Flask(__name__)

SERVICES = [
    {"name": "rabbit-care",       "port": 5200, "url": "http://localhost:5200", "launch_agent": True},
    {"name": "news-analyzer",     "port": 5300, "url": "http://localhost:5300", "launch_agent": True},
    {"name": "daytrade-replay",   "port": 5400, "url": "http://localhost:5400", "launch_agent": True},
    {"name": "stock-screener-ai", "port": 5500, "url": "http://localhost:5500", "launch_agent": True},
    {"name": "stock-screener",    "port": 5001, "url": "http://localhost:5001", "launch_agent": False},
]

CRON_TASKS = [
    {
        "name": "daytrade", "label": "當沖候選推播", "schedule": "09:30（週間）",
        "log": "/Users/steven/CCProject/logs/daytrade.log", "mtime_only": False,
    },
    {
        "name": "intraday", "label": "盤中監控", "schedule": "09:00（週間）",
        "log": "/tmp/intraday_monitor.log", "mtime_only": True,
    },
    {
        "name": "news", "label": "新聞 Pipeline", "schedule": "每15分鐘",
        "log": "/Users/steven/CCProject/news-analyzer/pipeline.log", "mtime_only": False,
    },
    {
        "name": "market", "label": "市場儀表板", "schedule": "每日07:30",
        "log": "/Users/steven/CCProject/logs/market-dashboard.log", "mtime_only": True,
    },
]


def check_service(svc: dict) -> dict:
    result = {**svc, "status": "down", "latency_ms": None}
    try:
        t0 = datetime.now()
        r = requests.get(f"{svc['url']}/api/health", timeout=2)
        ms = round((datetime.now() - t0).total_seconds() * 1000)
        if r.status_code < 500:
            result["status"] = "up"
            result["latency_ms"] = ms
    except requests.exceptions.Timeout:
        result["status"] = "timeout"
    except Exception:
        pass
    return result


def check_cron(task: dict) -> dict:
    today = date.today().isoformat()
    result = {"name": task["name"], "label": task["label"],
              "schedule": task["schedule"], "ran_today": False, "last_run": None}
    log_path = task["log"]
    if not os.path.exists(log_path):
        return result
    try:
        if task["mtime_only"]:
            mtime_date = date.fromtimestamp(os.path.getmtime(log_path))
            result["ran_today"] = (mtime_date == date.today())
            if result["ran_today"]:
                result["last_run"] = datetime.fromtimestamp(
                    os.path.getmtime(log_path)).strftime("%H:%M")
        else:
            with open(log_path, "r", errors="replace") as f:
                lines = f.readlines()[-300:]
            today_lines = [l for l in lines if today in l]
            if today_lines:
                result["ran_today"] = True
                ts = re.search(r"(\d{2}:\d{2})", today_lines[-1])
                if ts:
                    result["last_run"] = ts.group(1)
    except Exception:
        pass
    return result


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/status")
def api_status():
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(check_service, s): s for s in SERVICES}
        services = []
        for f in as_completed(futures):
            services.append(f.result())
    services.sort(key=lambda s: SERVICES.index(next(x for x in SERVICES if x["name"] == s["name"])))
    crons = [check_cron(t) for t in CRON_TASKS]
    return jsonify({
        "services": services,
        "crons": crons,
        "generated_at": datetime.now().isoformat(),
        "today": date.today().isoformat(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5600, debug=False)
