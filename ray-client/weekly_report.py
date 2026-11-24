"""每週 Ray 使用率報告：usage.py + health check → Telegram。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health
import usage

TOKEN_FILE = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
CHAT_ID = "7556217543"


def main() -> int:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        usage.report(7)
    usage_text = out.getvalue().strip()

    health_out = io.StringIO()
    with contextlib.redirect_stdout(health_out):
        health_code = health.main()
    health_text = health_out.getvalue().strip().splitlines()[0] if health_out.getvalue().strip() else "無輸出"

    text = (
        "📊 Ray 每週使用率報告\n\n"
        f"{usage_text}\n\n"
        f"健康檢查：{health_text}（exit={health_code}）"
    )

    token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode(),
    )
    json.load(urllib.request.urlopen(req, timeout=20))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
