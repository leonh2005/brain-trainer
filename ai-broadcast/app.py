"""ai-broadcast: 一次把問題廣播給多個 AI 網頁版，用系統 Firefox 已登入的帳號各開一個分頁自動送出。"""
import subprocess
from urllib.parse import quote

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# 每家的網頁版 URL 模板，{q} 會換成 URL-encode 後的問題。四家的自動送出行為都實測過。
# Gemini 官方不支援 URL 預填，改走 Google AI Mode（udm=50，同為 Gemini 驅動）。
SERVICES = {
    "gemini":     {"name": "Gemini",     "color": "#4285f4", "url": "https://www.google.com/search?udm=50&q={q}"},
    "chatgpt":    {"name": "ChatGPT",    "color": "#10a37f", "url": "https://chatgpt.com/?q={q}"},
    "grok":       {"name": "Grok",       "color": "#1a1a1a", "url": "https://grok.com/?q={q}"},
    "perplexity": {"name": "Perplexity", "color": "#20808d", "url": "https://www.perplexity.ai/search?q={q}"},
}


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.get("/")
def index():
    return render_template("index.html", services=SERVICES)


@app.post("/api/broadcast")
def broadcast():
    data = request.json or {}
    topic = (data.get("topic") or "").strip()
    targets = data.get("targets") or list(SERVICES)
    if not topic:
        return jsonify(error="問題不能為空"), 400
    targets = [t for t in targets if t in SERVICES]
    if not targets:
        return jsonify(error="沒有選任何 AI"), 400
    urls = [SERVICES[t]["url"].format(q=quote(topic)) for t in targets]
    # 用系統 Firefox（Steven 已登入的 profile）一次開多個分頁；argv 形式不經 shell，無注入風險
    subprocess.run(["open", "-a", "Firefox", *urls], check=True)
    return jsonify(opened=targets)


if __name__ == "__main__":
    app.run(port=5070)
