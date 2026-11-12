"""例外訊息中的憑證遮蔽（跨服務共用）。

背景
----
Shioaji SDK 的錯誤訊息會把整個請求 payload 印出來，裡面含 JWT access token
（2026-11-12 在 `logs/intraday_monitor.log` 實際看到 `'token': 'eyJ...'`）。
FinMind 與其他 HTTP 服務的例外也可能帶 `?token=` 或 `Authorization: Bearer`。
日誌是要長期保存與排查用的，不該沉澱可用憑證。

用法：所有「會把遠端例外的字串寫進日誌或回傳給呼叫端」的地方都包一層 redact()。
純函式、只依賴 re，可安全 import。
"""

import re

# JWT：三段 base64url，header 以 eyJ 開頭
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
# JSON / dict repr 裡的 token 欄位
# 負向先行：已替換過的佔位符不要再被吃一次（否則 <redacted-jwt> 會變 <redacted>）
_TOKEN_FIELD = re.compile(r"""(['"]token['"]\s*:\s*['"])(?!<redacted)[^'"]+(['"])""", re.I)
# query string 裡的 token / api_key
_QUERY_TOKEN = re.compile(r"([?&](?:token|access_token|api_key)=)[^&\s'\"]+", re.I)
# Authorization header
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)


def redact(value) -> str:
    """把字串中的 JWT／token 欄位／query token／Bearer 換成佔位符。冪等。"""
    text = str(value)
    text = _JWT.sub("<redacted-jwt>", text)
    text = _TOKEN_FIELD.sub(r"\1<redacted>\2", text)
    text = _QUERY_TOKEN.sub(r"\1<redacted>", text)
    text = _BEARER.sub(r"\1<redacted>", text)
    return text
