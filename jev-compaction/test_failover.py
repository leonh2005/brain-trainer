import http.client
import http.server
import json
import threading
import time

import pytest

import proxy


def _serve(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    status = 200
    seen: list = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        type(self).seen.append({"headers": dict(self.headers), "body": raw})
        payload = json.dumps({"type": "message", "from": self.server.name}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def _make_upstream(name, status):
    cls = type(f"Up_{name}", (_Upstream,), {"status": status, "seen": []})
    server = _serve(cls)
    server.name = name
    return server, cls


@pytest.fixture
def env(tmp_path, monkeypatch):
    primary, primary_cls = _make_upstream("abacus", 429)
    failover, failover_cls = _make_upstream("deepseek", 200)
    key_file = tmp_path / "ds.key"
    key_file.write_text("DS-KEY\n")
    monkeypatch.setattr(proxy, "UPSTREAM_SCHEME", "http")
    monkeypatch.setattr(proxy, "UPSTREAM_HOST", "127.0.0.1")
    monkeypatch.setattr(proxy, "UPSTREAM_PORT", primary.server_address[1])
    monkeypatch.setattr(proxy, "UPSTREAM_BASE", "")
    monkeypatch.setattr(proxy, "FAILOVER_SCHEME", "http")
    monkeypatch.setattr(proxy, "FAILOVER_HOST", "127.0.0.1")
    monkeypatch.setattr(proxy, "FAILOVER_PORT", failover.server_address[1])
    monkeypatch.setattr(proxy, "FAILOVER_BASE", "")
    monkeypatch.setattr(proxy, "FAILOVER_KEY_FILE", str(key_file))
    monkeypatch.setattr(proxy, "PRIMARY_HEALTH", str(tmp_path / "abacus-health.json"))
    monkeypatch.setattr(proxy, "FAILOVER_HEALTH", str(tmp_path / "ds-health.json"))
    monkeypatch.setattr(proxy, "PROBE_INTERVAL_S", 900.0)
    monkeypatch.setattr(proxy, "maybe_compact", lambda data: (data, "skip"))
    proxy_server = proxy.ThreadingHTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    threading.Thread(target=proxy_server.serve_forever, daemon=True).start()
    yield {
        "proxy_port": proxy_server.server_address[1],
        "primary_cls": primary_cls,
        "failover_cls": failover_cls,
        "primary_health": tmp_path / "abacus-health.json",
        "failover_health": tmp_path / "ds-health.json",
    }
    for s in (proxy_server, primary, failover):
        s.shutdown()
        s.server_close()


def _post(port, path, body, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"content-type": "application/json", "x-api-key": "ABACUS-KEY",
         "x-ccr-route-attempt": "1", "x-auth-sub": "u1"}
    h.update(headers or {})
    conn.request("POST", path, body=json.dumps(body), headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data)


MSG = {"model": "abacus::anthropic_messages/claude-haiku-5-5",
       "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}


def test_quota_error_fails_over_to_deepseek_with_its_key_and_model(env):
    status, data = _post(env["proxy_port"], "/v1/messages", MSG)

    assert status == 200
    assert data["from"] == "deepseek"
    sent = env["failover_cls"].seen[-1]
    assert sent["headers"]["x-api-key"] == "DS-KEY"
    assert "x-auth-sub" not in sent["headers"]
    assert "x-ccr-route-attempt" not in sent["headers"]
    assert json.loads(sent["body"])["model"] == "deepseek-flash"
    mark = json.loads(env["primary_health"].read_text())
    assert mark["status"] == 429


def test_success_clears_primary_mark_and_skips_failover(env):
    env["primary_health"].write_text(json.dumps({"status": 429, "ts": time.time()}))
    env["primary_cls"].status = 200
    before = len(env["failover_cls"].seen)

    status, data = _post(env["proxy_port"], "/v1/messages", MSG)

    assert status == 200 and data["from"] == "abacus"
    assert not env["primary_health"].exists()
    assert len(env["failover_cls"].seen) == before
    env["primary_cls"].status = 429


def test_connection_refused_on_primary_fails_over(env, monkeypatch):
    dead = http.server.ThreadingHTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    dead_port = dead.server_address[1]
    dead.server_close()
    monkeypatch.setattr(proxy, "UPSTREAM_PORT", dead_port)

    status, data = _post(env["proxy_port"], "/v1/messages", MSG)

    assert status == 200 and data["from"] == "deepseek"
    assert json.loads(env["primary_health"].read_text())["status"] == 0


def test_no_failover_while_deepseek_is_cooling(env):
    env["failover_health"].write_text(json.dumps({"status": 429, "ts": time.time()}))
    before = len(env["failover_cls"].seen)

    status, _ = _post(env["proxy_port"], "/v1/messages", MSG)

    assert status == 429
    assert len(env["failover_cls"].seen) == before


def test_failover_quota_error_marks_deepseek_cooling(env):
    env["failover_cls"].status = 402

    status, _ = _post(env["proxy_port"], "/v1/messages", MSG)

    assert status == 402
    assert json.loads(env["failover_health"].read_text())["status"] == 402
    env["failover_cls"].status = 200


def test_non_message_path_never_fails_over(env):
    conn = http.client.HTTPConnection("127.0.0.1", env["proxy_port"], timeout=10)
    conn.request("POST", "/v1/embeddings", body=b"{}", headers={"content-type": "application/json"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 429
    assert not env["primary_health"].exists()


def test_with_failover_model_rewrites_only_model():
    out = json.loads(proxy.with_failover_model(json.dumps(
        {"model": "x", "max_tokens": 3, "messages": []}).encode()))
    assert out == {"model": "deepseek-flash", "max_tokens": 3, "messages": []}


def test_with_failover_model_rejects_non_json():
    assert proxy.with_failover_model(b"not json") is None


def test_record_health_ignores_client_errors(tmp_path):
    path = tmp_path / "h.json"
    proxy.record_health(str(path), 400)
    assert not path.exists()


def test_record_health_clears_on_success(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{}")
    proxy.record_health(str(path), 200)
    assert not path.exists()


def test_is_cooling_respects_interval(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(proxy, "PROBE_INTERVAL_S", 900.0)
    path.write_text(json.dumps({"ts": time.time() - 100}))
    assert proxy.is_cooling(str(path)) is True
    path.write_text(json.dumps({"ts": time.time() - 1000}))
    assert proxy.is_cooling(str(path)) is False
    assert proxy.is_cooling(str(tmp_path / "missing.json")) is False


def test_failover_headers_drop_abacus_key_and_internal_headers():
    fwd = {"x-api-key": "ABACUS", "Authorization": "Bearer x", "x-ccr-route-attempt": "1",
           "x-auth-sub": "u", "anthropic-version": "2023-06-01"}
    out = proxy.build_headers(fwd, "https", "api.deepseek.com", 443, b"{}", api_key="DS")
    assert out["x-api-key"] == "DS"
    assert "Authorization" not in out
    assert "x-ccr-route-attempt" not in out and "x-auth-sub" not in out
    assert out["anthropic-version"] == "2023-06-01"
    assert out["Host"] == "api.deepseek.com"
