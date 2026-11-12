"""log_redact 測試：用 2026-11-12 實際出現在日誌裡的 payload 當樣本。"""

from log_redact import redact

# 自造的假 JWT（格式相同、內容無意義）——不可放真實 token，CCProject 是公開 repo
FAKE_JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0."
            "c2lnbmF0dXJlLW5vdC1yZWFs")
SDK_PAYLOAD = (
    "[avg5] 2409 第1次失敗: Topic: api/v1/data/kbars, Corr: c207, "
    f"payload: {{'token': '{FAKE_JWT}', "
    "'contract': {'security_type': 'STK', 'code': '2409'}}"
)


def test_redacts_sdk_exception_payload():
    out = redact(SDK_PAYLOAD)
    assert "eyJ" not in out
    assert "c2lnbmF0dXJl" not in out
    assert "'contract': {'security_type': 'STK', 'code': '2409'}" in out


def test_redacts_token_field_without_jwt_shape():
    assert redact("{'token': 'abc123def456'}") == "{'token': '<redacted>'}"
    assert redact('{"access_token": "ya29.abcdef"}') == '{"access_token": "<redacted>"}'
    assert redact("{'api_key': 'sk-123456'}") == "{'api_key': '<redacted>'}"


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
    once = redact(SDK_PAYLOAD)
    assert redact(once) == once


def test_accepts_non_string():
    assert redact(TimeoutError("boom")) == "boom"
