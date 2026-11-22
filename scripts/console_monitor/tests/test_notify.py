import cm_notify

ITEM = {
    "uid": "ruten|xsx|1",
    "console": "xsx",
    "source": "ruten",
    "name": 'XSX <1TB> & 二手 "不含手把"',
    "price": 15000,
    "seller": "電玩老頭",
    "posted_ts": 1795277000,
    "url": "https://www.ruten.com.tw/item/show?1",
}


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_format_item_escapes_html_and_plain_version_has_no_tags():
    html, plain = cm_notify.format_item(ITEM)
    assert "&lt;1TB&gt;" in html
    assert "&amp;" in html
    assert "&quot;不含手把&quot;" in html
    assert "<1TB>" in plain
    assert "&lt;" not in plain
    assert "<b>" not in plain


def test_classify_response_distinguishes_delivery_parse_issue_and_uncertainty():
    assert cm_notify.classify_response(200, {"ok": True}) == "delivered"
    assert cm_notify.classify_response(400, {"description": "can't parse entities"}) == "fallback"
    assert cm_notify.classify_response(400, {"description": "Bad Request: chat not found"}) == "permanent"
    assert cm_notify.classify_response(503, {"ok": False}) == "retryable"
    assert cm_notify.classify_response(None, None, timed_out=True) == "uncertain"


def test_send_returns_dry_run_without_calling_post():
    result = cm_notify.send(ITEM, dry_run=True, post=lambda **kw: None)
    assert result["status"] == "dry_run"


def test_send_uses_plain_text_fallback_when_html_is_rejected(monkeypatch):
    seen = []

    def post(**kw):
        seen.append(kw["json"])
        if len(seen) == 1:
            return FakeResponse(400, {"description": "can't parse entities"})
        return FakeResponse(200, {"ok": True, "result": {"message_id": 77}})

    monkeypatch.setattr(cm_notify.time, "sleep", lambda _: None)
    result = cm_notify.send(ITEM, post=post)
    assert result["status"] == "delivered"
    assert result["message_id"] == 77
    assert "parse_mode" not in seen[1]
    assert "parse_mode" in seen[0]


def test_send_does_not_retry_uncertain_timeout(monkeypatch):
    calls = []

    def post(**kw):
        calls.append(kw)
        raise TimeoutError("no response")

    monkeypatch.setattr(cm_notify.time, "sleep", lambda _: None)
    result = cm_notify.send(ITEM, post=post)
    assert result["status"] == "uncertain"
    assert len(calls) == 1


def test_send_retries_server_error_once(monkeypatch):
    calls = []

    def post(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return FakeResponse(500, {"ok": False})
        return FakeResponse(200, {"ok": True, "result": {"message_id": 88}})

    monkeypatch.setattr(cm_notify.time, "sleep", lambda _: None)
    result = cm_notify.send(ITEM, post=post)
    assert result["status"] == "delivered"
    assert len(calls) == 2


def test_rate_limit_is_treated_as_retryable():
    assert cm_notify.classify_response(429, {"description": "Too Many Requests: retry after 12"}) == "retryable"


def test_requests_timeout_is_uncertain_and_not_retried(monkeypatch):
    import requests

    calls = []

    def post(**kw):
        calls.append(kw)
        raise requests.exceptions.Timeout("read timeout")

    monkeypatch.setattr(cm_notify.time, "sleep", lambda _: None)
    result = cm_notify.send(ITEM, post=post)
    assert result["status"] == "uncertain"
    assert len(calls) == 1
