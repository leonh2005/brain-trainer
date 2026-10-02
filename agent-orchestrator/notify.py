"""Telegram 推播（只推給 Steven）。token 讀自 .secrets，讀不到或格式錯就靜默停用。"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / "CCProject" / ".secrets" / "telegram_token.txt"
CHAT_ID = "7556217543"


def _token():
    """讀 token；檔案不存在、讀不到、或不是 UTF-8 一律回 None（不停用整個服務也絕不拋）。"""
    try:
        return TOKEN_FILE.read_text().strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def send(text, chat_id=CHAT_ID, timeout=10):
    """推一則訊息；沒有 token 或失敗一律回 False，絕不讓例外影響呼叫端。"""
    try:
        token = _token()
        if not token:
            return False
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            return bool(json.loads(resp.read()).get("ok"))
    except Exception:
        return False
