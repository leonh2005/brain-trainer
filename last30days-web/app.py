"""last30days-web: 薄殼 Flask 包 last30days 引擎（觸發研究 + 進度 + 報告庫 + Groq 中文翻譯）"""
import glob
import logging
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
NEWS_DB = Path.home() / "CCProject" / "news-analyzer" / "news.db"
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
CORPUS_MAX_AGE = 6 * 3600  # 語料庫超過 6 小時才重新匯出
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


def refresh_corpus():
    """把 news-analyzer 近 35 天的新聞匯出成每日 md，給引擎 --corpus 搜尋"""
    import sqlite3
    from collections import defaultdict
    CORPUS_DIR.mkdir(exist_ok=True)
    stamp = CORPUS_DIR / ".last_export"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < CORPUS_MAX_AGE:
        return
    db = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    try:
        # PTT 舊文章 published_at 為 NULL，用 fetched_at 兜底才不會整批漏掉
        rows = db.execute("""
            SELECT date(COALESCE(published_at, fetched_at)), source, title,
                   COALESCE(summary, substr(content,1,300))
            FROM articles
            WHERE COALESCE(published_at, fetched_at) >= datetime('now','-35 days')
              AND COALESCE(published_at, fetched_at) <= datetime('now','+1 day')
              AND irrelevant = 0
            ORDER BY 1""").fetchall()
    finally:
        db.close()
    bydate = defaultdict(list)
    for d, src, title, body in rows:
        bydate[d].append(f"## {title}\n({src}, {d})\n{(body or '').strip()}\n")
    for old in CORPUS_DIR.glob("*.md"):
        old.unlink()
    for d, items in bydate.items():
        (CORPUS_DIR / f"{d}.md").write_text("\n".join(items))
    stamp.touch()


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
    try:
        refresh_corpus()
    except Exception as e:
        job["progress"] = f"新聞語料庫匯出失敗（不影響其他來源）：{e}"
    # --emit brief 才是完整合成報告；--emit html 只是給 LLM 宿主用的統計外殼
    # 檔名自己控制：引擎的 slug 會把中文主題整個吃掉
    stem = re.sub(r"[^\w一-鿿]+", "-", job["topic"]).strip("-") or "untitled"
    out_path = SAVE_DIR / f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    cmd = ["python3", str(engine), job["topic"], "--emit", "brief", "--output", str(out_path)]
    if any(CORPUS_DIR.glob("*.md")):
        cmd += ["--corpus", str(CORPUS_DIR)]
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
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError("引擎結束但沒有產出報告檔\n" + "\n".join(stderr_tail))
    job["file"] = out_path.name
    job["status"] = "done"


threading.Thread(target=worker, daemon=True).start()


def safe_report_path(filename):
    """白名單校驗：只允許 save-dir 內既有的 .md / .html 檔"""
    if filename != os.path.basename(filename) or not filename.endswith((".md", ".html")):
        abort(400)
    path = SAVE_DIR / filename
    if not path.is_file():
        abort(404)
    return path


@app.get("/api/health")
def health():
    return jsonify(status="ok", timestamp=datetime.now().isoformat())


def read_text_retry(path, size=None):
    """讀檔重試，扛 macOS 本機快照暫時鎖檔（EDEADLK）；重試後仍失敗則拋出原始例外"""
    for attempt in range(3):
        try:
            text = path.read_text(errors="replace")
            return text[:size] if size else text
        except OSError as e:
            if e.errno != 11 or attempt == 2:
                raise
            time.sleep(0.2)


def report_title(path):
    """md 讀首行標題、html 讀 <title>；檔名 slug 對中文主題會失真"""
    try:
        head = read_text_retry(path, size=2000)
    except OSError as e:
        logging.warning("report_title: 讀取 %s 失敗，改用檔名當標題 (%s)", path, e)
        return path.stem.replace("-", " ").replace("_", " ")
    m = (re.search(r"# Production Brief: (.+)", head) if path.suffix == ".md"
         else re.search(r"<title>last30days\s*·\s*([^<]+)</title>", head))
    return m.group(1).strip() if m else path.stem.replace("-", " ").replace("_", " ")


def zh_name_for(filename):
    base, ext = os.path.splitext(filename)
    return f"{base}.zh{ext}"


@app.get("/")
def index():
    reports = []
    files = list(SAVE_DIR.glob("*.md")) + list(SAVE_DIR.glob("*.html"))
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        if ".zh." in p.name:
            continue
        reports.append({
            "name": p.name,
            "title": report_title(p),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "has_zh": (SAVE_DIR / zh_name_for(p.name)).exists(),
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


MD_SHELL = """<!DOCTYPE html><html lang="zh-TW"><head><meta charset="utf-8">
<title>last30days · {title}</title>
<style>
  body {{ font: 16px/1.7 -apple-system, "PingFang TC", sans-serif; background: #12121f; color: #e8e8f0;
         max-width: 780px; margin: 0 auto; padding: 0 20px 60px; }}
  h1, h2, h3 {{ color: #fff; }} a {{ color: #7ec8ff; }}
  blockquote {{ border-left: 3px solid #444; margin-left: 0; padding-left: 14px; color: #aab; }}
  code {{ background: #1c1c2e; padding: 2px 5px; border-radius: 4px; }}
  hr {{ border: 0; border-top: 1px solid #333; }}
  em {{ color: #9ad; }}
</style></head><body>{toolbar}{content}</body></html>"""


@app.get("/report/<filename>")
def report(filename):
    from urllib.parse import quote
    path = safe_report_path(filename)
    is_zh = ".zh." in filename
    if is_zh:
        base, ext = filename.split(".zh.")
        other = quote(f"{base}.{ext}")
        switch = f'<a href="/report/{other}" style="color:#7ec8ff">看英文原版</a>'
    else:
        zh_name = zh_name_for(filename)
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
    content = read_text_retry(path)
    toolbar = TOOLBAR.format(switch=switch)
    if filename.endswith(".md"):
        import html as html_mod
        import markdown
        # 先跳脫再轉 markdown：報告內含未信任的網路文字，防 raw HTML 注入
        body = markdown.markdown(html_mod.escape(content), extensions=["tables"])
        return MD_SHELL.format(title=html_mod.escape(report_title(path)),
                               toolbar=toolbar, content=body)
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
                 "你是專業譯者。將使用者提供的 Markdown 或 HTML 片段中的可見文字翻譯成繁體中文（台灣用語）。"
                 "所有標記結構（HTML 標籤、Markdown 符號）、URL、程式碼、數字、專有名詞（人名/帳號/產品名）保持原樣。"
                 "只輸出翻譯後的內容，不要任何說明。"},
                {"role": "user", "content": text},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def chunk_html(content, limit=GROQ_CHUNK):
    """在段落或標籤邊界切塊，避免把單一段落/標籤切成兩半"""
    chunks = []
    while content:
        if len(content) <= limit:
            chunks.append(content)
            break
        cut = max(content.rfind("\n\n", 0, limit), content.rfind(">", 0, limit))
        if cut == -1:
            cut = limit - 1
        chunks.append(content[:cut + 1])
        content = content[cut + 1:]
    return chunks


@app.post("/api/translate/<filename>")
def translate(filename):
    path = safe_report_path(filename)
    if ".zh." in filename:
        return jsonify(error="這已經是中文版"), 400
    zh_path = SAVE_DIR / zh_name_for(filename)
    if zh_path.exists():
        return jsonify(file=zh_path.name)
    try:
        api_key = groq_api_key()
        content = read_text_retry(path)
        translated = "".join(groq_translate(c, api_key) for c in chunk_html(content))
        zh_path.write_text(translated)
        return jsonify(file=zh_path.name)
    except Exception as e:
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    app.run(port=5750)
