#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jev context 壓縮 proxy。

攔截送往 Anthropic API 的 /messages 請求，把較舊、已不相關的 tool 輸出
替換成一行移除通知，再轉發給下游 proxy。

鏈路（建議）：
  Claude Code (ANTHROPIC_BASE_URL=http://127.0.0.1:8788)
        │
        ▼
  proxy.py :8788      壓縮舊 tool_result（本檔）
        │
        ▼
  thinking-proxy :8787  思考開關（既有，不動）
        │
        ▼
  api.deepseek.com/anthropic

設計原則：
  - fail-open：任何壓縮環節出錯，一律原樣轉發，絕不讓對話失敗
  - 可完全繞過：把 ANTHROPIC_BASE_URL 改回 8787 即可停用
"""
from __future__ import annotations

import http.client
import http.server
import json
import logging
import os
import socketserver
from pathlib import Path

import compactor
import jev_client

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8788
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8787          # 下游＝thinking-proxy
UPSTREAM_BASE = "/anthropic"
LOG_PATH = Path(__file__).resolve().parent / "proxy.log"

# 是否啟用偽名化（隱私優先；關掉可省一點 CPU，但內容會原樣外送）
ANON = os.environ.get("JEV_COMPACTION_ANON", "1") != "0"
TIMEOUT = float(os.environ.get("JEV_COMPACTION_TIMEOUT", "10.0"))
# 上游（thinking-proxy）的等待上限。跟 JEV_COMPACTION_TIMEOUT 無關——
# 後者只管 Jev 呼叫。設太短會截斷長回應，太長則上游卡住時佔住執行緒。
UPSTREAM_TIMEOUT = float(os.environ.get("JEV_COMPACTION_UPSTREAM_TIMEOUT", "600"))

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(message)s")
log = logging.getLogger("jev-compaction-proxy")

_api_key: str | None = None
_cache = compactor._Cache()


def api_key() -> str | None:
    """延後載入並記住 key；載不到就回 None（fail-open）。"""
    global _api_key
    if _api_key is None:
        try:
            _api_key = jev_client.load_api_key()
        except Exception as e:  # noqa: BLE001
            log.warning("載入 Jev key 失敗，將停用壓縮：%s", e)
            return None
    return _api_key


def maybe_compact(data: dict) -> tuple[dict, str]:
    """對 messages 做壓縮。回傳 (body, 摘要)。任何錯誤都原樣返回。"""
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return data, "skip:no-messages"

    key = api_key()
    if not key:
        return data, "skip:no-key"

    try:
        new_messages, stats = compactor.compact(
            messages, key, anon=ANON, timeout=TIMEOUT, cache=_cache,
        )
    except Exception as e:  # noqa: BLE001 - fail-open
        log.warning("壓縮例外，原樣轉發：%s", e)
        return data, f"error:{type(e).__name__}"

    if new_messages is not messages:
        data = {**data, "messages": new_messages}
    return data, stats.summary()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 關閉預設 stderr 日誌

    def _forward(self, method):
        # 讀 body 必須 fail-open：Content-Length 若不是數字（畸形請求），
        # int() 會拋 ValueError 並逃出 handle_one_request，導致連線被關、請求不轉發。
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            log.info("[bad-content-length] %r", self.headers.get("Content-Length"))
            length = 0
        try:
            body = self.rfile.read(length) if length > 0 else b""
        except OSError as e:
            log.info("[read-error] %s", e)
            body = b""

        path_only = self.path.split("?", 1)[0]
        if method == "POST" and path_only.rstrip("/").endswith("/messages"):
            try:
                data = json.loads(body.decode("utf-8"))
                data, summary = maybe_compact(data)
                body = json.dumps(data).encode("utf-8")
                log.info("[compact] %s", summary)
            except Exception as e:  # noqa: BLE001
                log.info("[error] %s", e)

        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            fwd[k] = v
        fwd["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        fwd["Connection"] = "close"
        fwd["Content-Length"] = str(len(body))

        headers_sent = False
        conn = None
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT)
            conn.request(method, UPSTREAM_BASE + self.path, body=body, headers=fwd)
            resp = conn.getresponse()

            # 上游的 content-length 不轉發（SSE 沒有、且要邊讀邊送），
            # 但留著判斷「讀夠了沒」，免得只能等上游關閉連線。
            upstream_len = None
            for k, v in resp.getheaders():
                if k.lower() == "content-length":
                    try:
                        upstream_len = int(v)
                    except ValueError:
                        pass

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection", "content-length",
                          "keep-alive", "content-encoding"):
                    continue
                self.send_header(k, v)
            # 我們不送 content-length 也不送 chunked，長度只能靠關閉連線界定，
            # 所以必須明講 Connection: close，否則講究 framing 的客戶端會以為被截斷。
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True

            # 上游可能 keep-alive 不關，主動判斷結束：
            #   SSE → 收到 message_stop
            #   有 content-length → 讀足即停
            #   都沒有 → 只能等上游關閉
            tail = b""
            received = 0
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                received += len(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
                tail = (tail + chunk)[-65536:]   # 只留尾段比對，不再整包累積
                if b"message_stop" in tail:
                    break
                if upstream_len is not None and received >= upstream_len:
                    break
        except Exception as e:  # noqa: BLE001
            log.info("[upstream-error] %s", e)
            if not headers_sent:
                try:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({
                "status": "ok",
                "anon": ANON,
                "upstream": f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
                "key_loaded": api_key() is not None,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
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
    log.info("=== jev-compaction proxy started on %s:%s ===", LISTEN_HOST, LISTEN_PORT)
    print(f"jev-compaction proxy listening on {LISTEN_HOST}:{LISTEN_PORT} "
          f"(anon={ANON}, upstream={UPSTREAM_HOST}:{UPSTREAM_PORT})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
