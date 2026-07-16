"""last30days-web: 薄殼 Flask 包 last30days 引擎（觸發研究 + 進度 + 報告庫 + Groq 中文翻譯）"""
import glob
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, render_template, request

app = Flask(__name__)

SAVE_DIR = Path.home() / "Documents" / "Last30Days"
CONFIG_ENV = Path.home() / ".config" / "last30days" / ".env"
ENGINE_GLOB = str(Path.home() / ".claude/plugins/cache/last30days-skill/last30days/*/skills/last30days/scripts/last30days.py")
RUN_TIMEOUT = 900  # 單次研究上限 15 分鐘
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_CHUNK = 8000  # 每次送翻的字元數上限

jobs = {}  # job_id -> {status, topic, progress, error, file, created}
job_queue = queue.Queue()
submit_lock = threading.Lock()


def resolve_engine():
    """取最新版 plugin 的引擎路徑（plugin 自動更新後路徑會變）"""
    candidates = sorted(glob.glob(ENGINE_GLOB))
    if not candidates:
        raise FileNotFoundError("last30days engine not found; is the plugin installed?")
    return Path(candidates[-1])


def groq_api_key():
    for line in CONFIG_ENV.read_text().splitlines():
        if line.startswith("GROQ_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise KeyError("GROQ_API_KEY not found in " + str(CONFIG_ENV))


def worker():
    while True:
        job_id = job_queue.get()
        job = jobs[job_id]
        job["status"] = "running"
        try:
            run_engine(job)
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            job_queue.task_done()


def run_engine(job):
    engine = resolve_engine()
    started = time.time()
    before = set(SAVE_DIR.glob("*.html"))
    cmd = ["python3", str(engine), job["topic"], "--emit", "html", "--save-dir", str(SAVE_DIR)]
    if job["depth"] == "deep":
        cmd.append("--deep")
    else:
        cmd.append("--quick")
    proc = subprocess.Popen(
        cmd, cwd=engine.parents[2], stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True,
    )
    stderr_tail = []

    def read_stderr():
        for line in proc.stderr:
            line = line.strip()
            if not line:
                continue
            stderr_tail.append(line)
            del stderr_tail[:-20]
            job["progress"] = line

    t = threading.Thread(target=read_stderr, daemon=True)
    t.start()
    try:
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"研究超過 {RUN_TIMEOUT // 60} 分鐘，已強制終止")
    t.join(timeout=5)
    if proc.returncode != 0:
        raise RuntimeError("引擎執行失敗：\n" + "\n".join(stderr_tail))
    # 同主題重跑時引擎會覆寫同名檔，集合差會是空的，改用 mtime 兜底
    new_files = set(SAVE_DIR.glob("*.html")) - before
    if not new_files:
        new_files = {p for p in SAVE_DIR.glob("*.html") if p.stat().st_mtime >= started}
    if not new_files:
        raise RuntimeError("引擎結束但沒有產出報告檔\n" + "\n".join(stderr_tail))
    job["file"] = max(new_files, key=lambda p: p.stat().st_mtime).name
    job["status"] = "done"


threading.Thread(target=worker, daemon=True).start()


def safe_report_path(filename):
    """白名單校驗：只允許 save-dir 內既有的 .html 檔"""
    if filename != os.path.basename(filename) or not filename.endswith(".html"):
        abort(400)
    path = SAVE_DIR / filename
    if not path.is_file():
        abort(404)
    return path


@app.get("/api/health")
def health():
    return jsonify(status="ok", timestamp=datetime.now().isoformat())


@app.get("/")
def index():
    reports = []
    for p in sorted(SAVE_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.name.endswith(".zh.html"):
            continue
        reports.append({
            "name": p.name,
            "title": p.stem.replace("-", " ").replace("_", " "),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "has_zh": (SAVE_DIR / (p.stem + ".zh.html")).exists(),
        })
    running = any(j["status"] in ("queued", "running") for j in jobs.values())
    return render_template("index.html", reports=reports, running=running)


@app.post("/api/run")
def run():
    topic = (request.json or {}).get("topic", "").strip()
    depth = (request.json or {}).get("depth", "quick")
    if not topic:
        return jsonify(error="主題不能為空"), 400
    if topic.startswith("-"):
        return jsonify(error="主題不能以 - 開頭"), 400
    if depth not in ("quick", "deep"):
        return jsonify(error="depth 只能是 quick 或 deep"), 400
    with submit_lock:
        if any(j["status"] in ("queued", "running") for j in jobs.values()):
            return jsonify(error="已有研究在跑，請等它完成"), 409
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {
            "status": "queued", "topic": topic, "depth": depth,
            "progress": "排隊中…", "error": None, "file": None,
            "created": time.time(),
        }
        job_queue.put(job_id)
    return jsonify(job_id=job_id), 202


@app.get("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify({k: job[k] for k in ("status", "topic", "progress", "error", "file")})


TOOLBAR = """
<div style="position:sticky;top:0;z-index:9999;background:#1a1a2e;color:#eee;
            padding:10px 16px;font:14px -apple-system,sans-serif;display:flex;gap:16px;align-items:center">
  <a href="/" style="color:#7ec8ff;text-decoration:none">← 回列表</a>
  <span style="flex:1"></span>
  {switch}
</div>
"""


@app.get("/report/<filename>")
def report(filename):
    from urllib.parse import quote
    path = safe_report_path(filename)
    is_zh = filename.endswith(".zh.html")
    if is_zh:
        other = quote(filename[:-8] + ".html")
        switch = f'<a href="/report/{other}" style="color:#7ec8ff">看英文原版</a>'
    else:
        zh_name = filename[:-5] + ".zh.html"
        if (SAVE_DIR / zh_name).exists():
            switch = f'<a href="/report/{quote(zh_name)}" style="color:#7ec8ff">看中文版</a>'
        else:
            switch = (f'<button onclick="translateReport()" id="tbtn" '
                      f'style="background:#7ec8ff;border:0;border-radius:4px;padding:6px 14px;cursor:pointer">翻成中文</button>'
                      f'<script>async function translateReport(){{'
                      f'const b=document.getElementById("tbtn");b.disabled=true;b.textContent="翻譯中…（約1-2分鐘）";'
                      f'const r=await fetch("/api/translate/{quote(filename)}",{{method:"POST"}});'
                      f'const d=await r.json();'
                      f'if(r.ok)location.href="/report/"+encodeURIComponent(d.file);'
                      f'else{{b.textContent="翻譯失敗";alert(d.error)}}}}</script>')
    content = path.read_text(errors="replace")
    toolbar = TOOLBAR.format(switch=switch)
    if "<body" in content:
        content = re.sub(r"(<body[^>]*>)", r"\1" + toolbar.replace("\\", "\\\\"), content, count=1)
    else:
        content = toolbar + content
    return content


def groq_translate(text, api_key):
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": GROQ_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content":
                 "你是專業譯者。將使用者提供的 HTML 片段中的可見文字翻譯成繁體中文（台灣用語）。"
                 "所有 HTML 標籤、屬性、URL、程式碼、數字、專有名詞（人名/帳號/產品名）保持原樣。"
                 "只輸出翻譯後的 HTML，不要任何說明。"},
                {"role": "user", "content": text},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def chunk_html(content, limit=GROQ_CHUNK):
    """在標籤邊界切塊，避免把單一標籤切成兩半"""
    chunks = []
    while content:
        if len(content) <= limit:
            chunks.append(content)
            break
        cut = content.rfind(">", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(content[:cut + 1])
        content = content[cut + 1:]
    return chunks


@app.post("/api/translate/<filename>")
def translate(filename):
    path = safe_report_path(filename)
    if filename.endswith(".zh.html"):
        return jsonify(error="這已經是中文版"), 400
    zh_path = SAVE_DIR / (filename[:-5] + ".zh.html")
    if zh_path.exists():
        return jsonify(file=zh_path.name)
    try:
        api_key = groq_api_key()
        content = path.read_text(errors="replace")
        translated = "".join(groq_translate(c, api_key) for c in chunk_html(content))
        zh_path.write_text(translated)
        return jsonify(file=zh_path.name)
    except Exception as e:
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    app.run(port=5750)
