#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek thinking proxy for Claude Code.

攔截 Claude Code → DeepSeek 的請求，根據 prompt 內容判斷是否啟用 thinking：
默認關閉 thinking 省 token，偵測到「思考型任務」關鍵字才開啟。

流程：
  Claude Code (ANTHROPIC_BASE_URL=http://127.0.0.1:8787)
        │
        ▼
  proxy.py  判斷 prompt → 決定 thinking 開/關
        │
        ▼
  api.deepseek.com/anthropic/v1/messages

判斷規則（decide_thinking）：
  - body 已有 thinking:disabled（Claude Code 對子代理的行為）→ 不覆蓋
  - 其餘（主模型）→ 依關鍵字判斷，命中開 thinking，否則關
"""

import http.client
import http.server
import json
import logging
import socketserver
from pathlib import Path

UPSTREAM_HOST = "api.deepseek.com"
UPSTREAM_BASE = "/anthropic"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8787
LOG_PATH = Path(__file__).resolve().parent / "proxy.log"

# 思考型任務關鍵字：命中才開 thinking
THINKING_KEYWORDS = [
    "分析", "除錯", "診斷", "排查", "根源", "為什麼", "原因",
    "設計", "規劃", "架構", "方案", "重構", "評估", "比較", "檢討",
    "優化", "權衡", "取捨", "策略", "回顧", "review",
    "debug", "analy", "investigat", "design", "architect",
    "refactor", "root cause", "tradeoff", "trade-off",
    "diagnos", "troubleshoot",
]

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)


def extract_text(body):
    """從 request body 抽出所有文字內容，供關鍵字判斷。"""
    parts = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    for m in body.get("messages", []):
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
    return "\n".join(parts)


def decide_thinking(body):
    """決定 thinking 參數。

    回傳 (是否改寫, thinking 值, 判斷標籤)。
    - 已有 thinking:disabled（子代理）→ 不覆蓋，label=keep
    - 其餘（主模型）→ 依關鍵字判斷
    """
    existing = body.get("thinking")
    if isinstance(existing, dict) and existing.get("type") == "disabled":
        return False, None, "keep"

    text = extract_text(body)
    if any(k.lower() in text.lower() for k in THINKING_KEYWORDS):
        return True, {"type": "enabled"}, "on"
    return True, {"type": "disabled"}, "off"


def log_decision(label, body):
    text = extract_text(body).strip().replace("\n", " ")
    logging.info("[%s] %s", label, text[:100])


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 關閉預設 stderr 日誌

    def _forward(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""

        # 僅對 messages 端點做 thinking 判斷
        if method == "POST" and self.path.rstrip("/").endswith("/messages"):
            try:
                data = json.loads(body.decode("utf-8"))
                should_rewrite, thinking, label = decide_thinking(data)
                if should_rewrite:
                    data["thinking"] = thinking
                    body = json.dumps(data).encode("utf-8")
                log_decision(label, data)
            except Exception as e:
                logging.info("[error] %s", e)

        # 轉發 headers（去掉 hop-by-hop）
        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            fwd[k] = v
        fwd["Host"] = UPSTREAM_HOST
        fwd["Connection"] = "close"
        fwd["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=600)
            conn.request(method, UPSTREAM_BASE + self.path, body=body, headers=fwd)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection", "content-length", "keep-alive"):
                    continue
                self.send_header(k, v)
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            conn.close()
        except Exception as e:
            logging.info("[upstream-error] %s", e)
            try:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            except Exception:
                pass
        finally:
            self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PUT(self):
        self._forward("PUT")

    def do_DELETE(self):
        self._forward("DELETE")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    logging.info("=== proxy started on %s:%s ===", LISTEN_HOST, LISTEN_PORT)
    print(f"thinking-proxy listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
