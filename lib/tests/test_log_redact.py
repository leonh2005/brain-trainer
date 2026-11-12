"""log_redact 測試：用 2026-11-12 實際出現在日誌裡的 payload 當樣本。"""

from log_redact import redact

# 取自 logs/intraday_monitor.log 的真實 SDK 例外（token 已截短但格式相同）
REAL_PAYLOAD = (
    "[avg5] 2409 第1次失敗: Topic: api/v1/data/kbars, Corr: c207, "
    "payload: {'token': 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
    "eyJuYmYiOjE3OTQ0NDY0MDgsImV4cCI6MTc5NDUzMjgwOH0."
    "oypM06XlcnJaXq3phV-mRYiVNjeeh5rvbjqY1KPS9l4', "
    "'contract': {'security_type': 'STK', 'code': '2409'}}"
)


def test_redacts_real_jwt_payload():
    out = redact(REAL_PAYLOAD)
    assert "eyJ" not in out
    assert "oypM06Xl" not in out
    assert "'contract': {'security_type': 'STK', 'code': '2409'}" in out


def test_redacts_token_field_without_jwt_shape():
    assert redact("{'token': 'abc123def456'}") == "{'token': '<redacted>'}"


def test_redacts_query_token():
    out = redact("GET https://api.finmindtrade.com/api/v4/data?token=abc123&x=1")
    assert "abc123" not in out
    assert "token=<redacted>" in out


def test_redacts_bearer_header():
    out = redact("Authorization: Bearer sk-abcdef123456")
    assert "sk-abcdef" not in out
    assert "Bearer <redacted>" in out


def test_non_token_text_unchanged():
    text = "login already in progress"
    assert redact(text) == text


def test_idempotent():
    once = redact(REAL_PAYLOAD)
    assert redact(once) == once


def test_accepts_non_string():
    assert redact(TimeoutError("boom")) == "boom"
