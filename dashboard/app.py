#!/usr/bin/env python3
"""Steven's Dashboard — 服務監控中心 (port 5600)"""

import os
import re
import subprocess
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify
import requests

app = Flask(__name__)

VM_IP = "161.33.6.190"
VM_SSH_KEY = os.path.expanduser("~/.ssh/oracle_line_bot")

# ── 本地 Flask 服務 ──────────────────────────────────────────
LOCAL_SERVICES = [
    {"name": "rabbit-care",       "port": 5200, "url": "http://localhost:5200", "launch_agent": True},
    {"name": "news-analyzer",     "port": 5300, "url": "http://localhost:5300", "launch_agent": True},
    {"name": "daytrade-replay",   "port": 5400, "url": "http://localhost:5400", "launch_agent": True},
    {"name": "stock-screener-ai", "port": 5500, "url": "http://localhost:5500", "launch_agent": True},
    {"name": "stock-screener",    "port": 5001, "url": "http://localhost:5001", "launch_agent": False},
    {"name": "banini-tracker",    "port": 3099, "url": "http://localhost:3099", "launch_agent": True},
]

# ── VM Flask 服務 ─────────────────────────────────────────────
VM_SERVICES = [
    {"name": "tele-bot",          "port": 5000, "url": f"http://{VM_IP}:5000", "host": "VM"},
    {"name": "stock-screener-vm", "port": 5001, "url": f"http://{VM_IP}:5001", "host": "VM"},
]

# ── 背景程序（log mtime 判斷是否存活）──────────────────────────
BACKGROUND_PROCS = [
    {
        "name": "youtube-monitor",
        "label": "YouTube 監測器",
        "log": "/Users/steven/youtube-monitor/monitor.log",
        "alive_minutes": 90,
    },
    {
        "name": "motion-watcher",
        "label": "墨墨行為辨識",
        "log": "/Users/steven/CCProject/rabbit-care/motion-watcher.log",
        "alive_minutes": 10,
    },
    {
        "name": "rabbit-tunnel",
        "label": "Rabbit Tunnel",
        "log": "/Users/steven/CCProject/rabbit-care/tunnel.log",
        "alive_minutes": 30,
    },
]

# ── 本地排程任務 ──────────────────────────────────────────────
LOCAL_CRONS = [
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
    {
        "name": "youtube-rss", "label": "YouTube RSS 監測", "schedule": "每小時整點",
        "log": "/Users/steven/youtube-monitor/monitor.log", "keyword": "Job.*check_and_notify.*executed successfully",
    },
    {
        "name": "youtube-4k", "label": "4K 新片監測", "schedule": "每日20:00",
        "log": "/Users/steven/youtube-monitor/monitor.log", "keyword": "4K",
    },
    {
        "name": "douyin", "label": "抖音監測", "schedule": "每小時:15",
        "log": "/Users/steven/youtube-monitor/monitor.log", "keyword": "抖音監測完成",
    },
    {
        "name": "github-research", "label": "GitHub 每日研究", "schedule": "每日08:00",
        "log": "/Users/steven/youtube-monitor/monitor.log", "keyword": "github_research.*executed",
    },
    {
        "name": "vm-connectivity", "label": "VM 連線監測", "schedule": "每小時:30",
        "log": "/Users/steven/youtube-monitor/monitor.log", "keyword": "check_vm_connectivity.*executed",
    },
]

# ── VM 排程任務（SSH grep journald）──────────────────────────
VM_CRONS = [
    {"name": "lotto",   "label": "大樂透監測",   "schedule": "週二、五 21:00", "keyword": "樂透"},
    {"name": "steam",   "label": "Steam 免費遊戲", "schedule": "每小時",        "keyword": "steam"},
    {"name": "food",    "label": "食物效期提醒",  "schedule": "每日12:00",      "keyword": "效期"},
    {"name": "vm-health","label": "VM 健康檢查",  "schedule": "每日13:00",      "keyword": "健康檢查"},
]


def check_service(svc: dict, timeout: int = 2) -> dict:
    result = {**svc, "status": "down", "latency_ms": None}
    try:
        t0 = datetime.now()
        r = requests.get(f"{svc['url']}/api/health", timeout=timeout)
        ms = round((datetime.now() - t0).total_seconds() * 1000)
        if r.status_code < 500:
            result["status"] = "up"
            result["latency_ms"] = ms
    except requests.exceptions.Timeout:
        result["status"] = "timeout"
    except Exception:
        pass
    return result


def check_background(proc: dict) -> dict:
    result = {"name": proc["name"], "label": proc["label"], "status": "down", "last_seen": None}
    log = proc["log"]
    if not os.path.exists(log):
        return result
    try:
        mtime = os.path.getmtime(log)
        age_min = (datetime.now().timestamp() - mtime) / 60
        if age_min <= proc["alive_minutes"]:
            result["status"] = "up"
        result["last_seen"] = datetime.fromtimestamp(mtime).strftime("%H:%M")
    except Exception:
        pass
    return result


def check_local_cron(task: dict) -> dict:
    today = date.today().isoformat()
    result = {"name": task["name"], "label": task["label"],
              "schedule": task["schedule"], "ran_today": False, "last_run": None}
    log_path = task["log"]
    if not os.path.exists(log_path):
        return result
    try:
        if task.get("mtime_only"):
            mtime_date = date.fromtimestamp(os.path.getmtime(log_path))
            result["ran_today"] = (mtime_date == date.today())
            if result["ran_today"]:
                result["last_run"] = datetime.fromtimestamp(
                    os.path.getmtime(log_path)).strftime("%H:%M")
        else:
            keyword = task.get("keyword")
            with open(log_path, "r", errors="replace") as f:
                lines = f.readlines()[-500:]
            today_lines = [l for l in lines if today in l and (not keyword or re.search(keyword, l))]
            if today_lines:
                result["ran_today"] = True
                ts = re.search(r"(\d{2}:\d{2})", today_lines[-1])
                if ts:
                    result["last_run"] = ts.group(1)
    except Exception:
        pass
    return result


def check_vm_crons() -> list:
    today = date.today().strftime("%b %d").lstrip("0") if date.today().day >= 10 else date.today().strftime("%b %d")
    today_iso = date.today().isoformat()
    results = []
    try:
        cmd = [
            "ssh", "-i", VM_SSH_KEY, "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5", f"ubuntu@{VM_IP}",
            f"journalctl -u tele-bot --since today --no-pager -q 2>/dev/null | tail -200"
        ]
        out = subprocess.check_output(cmd, timeout=8, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
        for task in VM_CRONS:
            ran = bool(re.search(task["keyword"], out, re.IGNORECASE))
            last_run = None
            if ran:
                matches = list(re.finditer(r"(\d{2}:\d{2}:\d{2}).*" + task["keyword"], out, re.IGNORECASE))
                if matches:
                    last_run = matches[-1].group(1)[:5]
            results.append({
                "name": task["name"], "label": task["label"],
                "schedule": task["schedule"], "ran_today": ran,
                "last_run": last_run, "source": "vm",
            })
    except Exception:
        for task in VM_CRONS:
            results.append({
                "name": task["name"], "label": task["label"],
                "schedule": task["schedule"], "ran_today": False,
                "last_run": None, "source": "vm_offline",
            })
    return results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/status")
def api_status():
    all_svcs = LOCAL_SERVICES + VM_SERVICES
    with ThreadPoolExecutor(max_workers=10) as ex:
        timeout_map = {s["name"]: (5 if s.get("host") == "VM" else 2) for s in all_svcs}
        futures = {ex.submit(check_service, s, timeout_map[s["name"]]): s for s in all_svcs}
        svc_results = {}
        for f in as_completed(futures):
            r = f.result()
            svc_results[r["name"]] = r

    local_services = [svc_results[s["name"]] for s in LOCAL_SERVICES]
    vm_services = [svc_results[s["name"]] for s in VM_SERVICES]

    bg_procs = [check_background(p) for p in BACKGROUND_PROCS]
    local_crons = [check_local_cron(t) for t in LOCAL_CRONS]
    vm_crons = check_vm_crons()

    return jsonify({
        "local_services": local_services,
        "vm_services": vm_services,
        "bg_procs": bg_procs,
        "local_crons": local_crons,
        "vm_crons": vm_crons,
        "generated_at": datetime.now().isoformat(),
        "today": date.today().isoformat(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5600, debug=False)
