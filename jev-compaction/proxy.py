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
import re
import socketserver
import time
from datetime import datetime
from pathlib import Path

import compactor
import jev_client

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("JEV_COMPACTION_LISTEN_PORT", "8788"))

# 上游＝真正的 LLM 供應商。
#
# 接法：ccr 的 deepseek provider 有一個 api_base_url
# (`https://api.deepseek.com/anthropic`)，把它改成 http://127.0.0.1:8788，
# ccr 就會把請求送來這裡，我們壓縮後再轉給真正的 DeepSeek。
# 這樣做不用碰 ccr 管理的 settings.json／wrapper（那些會被覆蓋）。
UPSTREAM_SCHEME = os.environ.get("JEV_COMPACTION_UPSTREAM_SCHEME", "https")
UPSTREAM_HOST = os.environ.get("JEV_COMPACTION_UPSTREAM_HOST", "api.deepseek.com")
UPSTREAM_PORT = int(os.environ.get("JEV_COMPACTION_UPSTREAM_PORT", "443"))
UPSTREAM_BASE = os.environ.get("JEV_COMPACTION_UPSTREAM_BASE", "/anthropic")

# 額度切換：Abacus（主）→ DeepSeek（備）→ DeepSeek 也用完時再回 Abacus。
# 冷卻標記記錄「哪一邊最近額度用盡或連線失敗」，冷卻期（PROBE_INTERVAL_S）過後放行一個請求試探。
PRIMARY_HEALTH = os.environ.get("JEV_COMPACTION_HEALTH_FILE", "")
FAILOVER_HEALTH = os.environ.get("JEV_COMPACTION_FAILOVER_HEALTH_FILE", "")
FAILOVER_KEY_FILE = os.environ.get("JEV_COMPACTION_FAILOVER_KEY_FILE", "")
FAILOVER_SCHEME = os.environ.get("JEV_COMPACTION_FAILOVER_SCHEME", "https")
FAILOVER_HOST = os.environ.get("JEV_COMPACTION_FAILOVER_HOST", "api.deepseek.com")
FAILOVER_PORT = int(os.environ.get("JEV_COMPACTION_FAILOVER_PORT", "443"))
FAILOVER_BASE = os.environ.get("JEV_COMPACTION_FAILOVER_BASE", "/anthropic")
FAILOVER_MODEL = os.environ.get("JEV_COMPACTION_FAILOVER_MODEL", "deepseek-flash")
PROBE_INTERVAL_S = float(os.environ.get("JEV_COMPACTION_PROBE_INTERVAL_S", "900"))
QUOTA_STATUS = {402, 403, 429}
FAILOVER_STATUS = QUOTA_STATUS | set(range(500, 600))
INTERNAL_HEADER_PREFIXES = ("x-ccr-", "x-auth-", "x-target-", "x-gateway-")

LOG_PATH = Path(os.environ.get("JEV_COMPACTION_LOG",
                               Path(__file__).resolve().parent / "proxy.log"))

# 偽名化（把路徑／專案名換成代號）。
# 預設關閉：使用者只要求「憑證不能洩漏」，不在意對話內容的去識別化。
# 而偽名化會微幅干擾 Jev 的判斷（實測一致率 98.6%），沒必要開著。
# 需要時設 JEV_COMPACTION_ANON=1 開啟。
# 注意：憑證遮蔽（redact）不受這個開關影響，永遠開啟。
ANON = os.environ.get("JEV_COMPACTION_ANON", "0") == "1"

# 影子模式：評估但不實際改動送出的內容。
# 接上真實鏈路前先用它觀察「會砍什麼」，風險為零（只多花一點 Jev 費用）。
DRY_RUN = os.environ.get("JEV_COMPACTION_DRY_RUN", "0") == "1"
# A/B 對照：開啟時只在奇數日壓縮，偶數日原樣轉發。
AB_MODE = os.environ.get("JEV_COMPACTION_AB", "0") == "1"
TIMEOUT = float(os.environ.get("JEV_COMPACTION_TIMEOUT", "10.0"))
# 上游（thinking-proxy）的等待上限。跟 JEV_COMPACTION_TIMEOUT 無關——
# 後者只管 Jev 呼叫。設太短會截斷長回應，太長則上游卡住時佔住執行緒。
UPSTREAM_TIMEOUT = float(os.environ.get("JEV_COMPACTION_UPSTREAM_TIMEOUT", "600"))

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(message)s")
log = logging.getLogger("jev-compaction-proxy")

_api_key: str | None = None
_cache = compactor._Cache()


_USAGE_FIELDS = {
    "in": re.compile(rb'"input_tokens":\s*(\d+)'),
    "cache_read": re.compile(rb'"cache_read_input_tokens":\s*(\d+)'),
    "cache_create": re.compile(rb'"cache_creation_input_tokens":\s*(\d+)'),
    "out": re.compile(rb'"output_tokens":\s*(\d+)'),
}


def usage_summary(group: str, head: bytes, tail: bytes) -> str:
    """從串流的開頭（message_start）與尾段（message_delta）取出 usage。"""
    def last(pattern: re.Pattern, buf: bytes) -> str:
        found = pattern.findall(buf)
        return found[-1].decode() if found else "-"

    return " ".join([
        group or "-",
        f"in={last(_USAGE_FIELDS['in'], head)}",
        f"cache_read={last(_USAGE_FIELDS['cache_read'], head)}",
        f"cache_create={last(_USAGE_FIELDS['cache_create'], head)}",
        f"out={last(_USAGE_FIELDS['out'], tail)}",
    ])


def record_health(path: str, status: int, detail: str = "") -> None:
    """額度用盡或連線失敗就寫冷卻標記；上游成功則清除。其他狀態碼不動標記。"""
    if not path or not (status == 0 or status in QUOTA_STATUS or 200 <= status < 300):
        return
    target = Path(path)
    try:
        if 200 <= status < 300:
            target.unlink(missing_ok=True)
            return
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"status": status, "ts": time.time(), "detail": detail[:300]}))
        tmp.replace(target)
    except OSError as e:  # noqa: BLE001 - 標記寫失敗不能影響轉發
        log.warning("寫冷卻標記失敗：%s", e)


def is_cooling(path: str) -> bool:
    if not path:
        return False
    try:
        mark = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False
    return time.time() - float(mark.get("ts", 0)) < PROBE_INTERVAL_S


def failover_key() -> str | None:
    if not FAILOVER_KEY_FILE:
        return None
    try:
        return Path(FAILOVER_KEY_FILE).read_text().strip() or None
    except OSError:
        return None


def with_failover_model(body: bytes) -> bytes | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data["model"] = FAILOVER_MODEL
    return json.dumps(data).encode("utf-8")


def build_headers(fwd: dict, scheme: str, host: str, port: int, payload: bytes,
                  api_key: str | None = None) -> dict:
    headers = dict(fwd)
    if api_key is not None:
        # 改送備援上游時換掉認證，並去掉 ccr 內部標頭，不把 Abacus 的 key 或路由資訊送給 DeepSeek
        headers = {k: v for k, v in headers.items()
                   if not k.lower().startswith(INTERNAL_HEADER_PREFIXES)
                   and k.lower() not in ("x-api-key", "authorization")}
        headers["x-api-key"] = api_key
    default_port = 443 if scheme == "https" else 80
    headers["Host"] = host if port == default_port else f"{host}:{port}"
    headers["Connection"] = "close"
    headers["Content-Length"] = str(len(payload))
    return headers


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

    if AB_MODE and datetime.now().day % 2 == 0:
        return data, "ab-control"

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

    if DRY_RUN:
        # 影子模式：照常評估（會花 Jev 費用），但送出原始內容。
        # 用來觀察「它會砍什麼」而不承擔任何風險。
        return data, f"dry-run | {stats.summary()}"

    if new_messages is not messages:
        data = {**data, "messages": new_messages}
    return data, stats.summary()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 關閉預設 stderr 日誌

    def _open(self, method, body, scheme, host, port, base, headers):
        conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(host, port, timeout=UPSTREAM_TIMEOUT)
        try:
            conn.request(method, base + self.path, body=body, headers=headers)
            return conn, conn.getresponse()
        except Exception:
            conn.close()
            raise

    def _open_failover(self, method, body, fwd):
        key = failover_key()
        payload = with_failover_model(body)
        if key is None or payload is None:
            raise OSError("failover unavailable")
        headers = build_headers(fwd, FAILOVER_SCHEME, FAILOVER_HOST, FAILOVER_PORT,
                                payload, api_key=key)
        log.info("[failover] -> %s/%s", FAILOVER_HOST, FAILOVER_MODEL)
        conn, resp = self._open(method, payload, FAILOVER_SCHEME, FAILOVER_HOST,
                                FAILOVER_PORT, FAILOVER_BASE, headers)
        record_health(FAILOVER_HEALTH, resp.status)
        return conn, resp

    def _open_with_failover(self, method, body, fwd, is_msg):
        """回傳 (conn, resp)。Abacus 額度用盡或連不上時，同一個請求改由 DeepSeek 回應。"""
        primary = build_headers(fwd, UPSTREAM_SCHEME, UPSTREAM_HOST, UPSTREAM_PORT, body)
        can_failover = (is_msg and failover_key() is not None
                        and not is_cooling(FAILOVER_HEALTH))
        try:
            conn, resp = self._open(method, body, UPSTREAM_SCHEME, UPSTREAM_HOST,
                                    UPSTREAM_PORT, UPSTREAM_BASE, primary)
        except OSError as e:
            if is_msg:
                record_health(PRIMARY_HEALTH, 0, str(e))
            if not can_failover:
                raise
            return self._open_failover(method, body, fwd)
        if not is_msg:
            return conn, resp
        if resp.status in FAILOVER_STATUS and can_failover:
            detail = resp.read(4096).decode("utf-8", "replace")
            conn.close()
            record_health(PRIMARY_HEALTH, resp.status, detail)
            return self._open_failover(method, body, fwd)
        record_health(PRIMARY_HEALTH, resp.status)
        return conn, resp

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
        group = ""
        is_msg = method == "POST" and path_only.rstrip("/").endswith("/messages")
        if is_msg:
            try:
                data = json.loads(body.decode("utf-8"))
                data, summary = maybe_compact(data)
                body = json.dumps(data).encode("utf-8")
                group = ("AB:ctrl" if summary == "ab-control"
                         else "AB:on" if AB_MODE else "")
                log.info("[compact]%s %s", f" {group}" if group else "", summary)
            except Exception as e:  # noqa: BLE001
                log.info("[error] %s", e)

        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            fwd[k] = v
        headers_sent = False
        head = b""
        conn = None
        try:
            conn, resp = self._open_with_failover(method, body, fwd, is_msg)

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
                if len(head) < 8192:
                    head += chunk[:8192 - len(head)]
                if b"message_stop" in tail:
                    break
                if upstream_len is not None and received >= upstream_len:
                    break
            if b'"usage"' in head or b'"usage"' in tail:
                log.info("[usage] %s", usage_summary(group, head, tail))
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
                "dry_run": DRY_RUN,
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
