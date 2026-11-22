"""逐筆 Telegram 通知：HTML 跳脫、純文字 fallback、結果分類。"""
from __future__ import annotations

import html
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import requests
from cm_config import CHAT_ID, CONSOLES, TPE


def format_item(item: dict[str, Any]) -> tuple[str, str]:
    cfg = CONSOLES[item["console"]]
    name = item.get("name") or ""
    seller = item.get("seller") or ""
    price = f"NT${item['price']:,}" if item.get("price") else "價格見內文"
    posted = item.get("posted_ts")
    when = datetime.fromtimestamp(posted, TPE).strftime("%Y-%m-%d %H:%M") if posted else "未知"
    hints = "、".join(item.get("hints") or [])
    source = item.get("source") or ""
    url = item.get("url") or ""

    safe_name = html.escape(name, quote=True)
    safe_seller = html.escape(seller, quote=True)
    hint_line = f"提示：{html.escape(hints)}\n" if hints else ""

    text = (
        f"🎮 <b>{cfg['label']} 二手新刊登</b>（{html.escape(source)}）\n\n"
        f"{safe_name}\n"
        f"💰 <b>{price}</b>\n"
        f"🕒 上架：{when}\n"
        f"{hint_line}"
        f"👤 賣家：{safe_seller}\n"
        f"🔗 {url}"
    )
    plain = (
        f"{cfg['label']} 二手新刊登（{source}）\n\n"
        f"{name}\n"
        f"價格：{price}\n"
        f"上架：{when}\n"
        f"{'提示：' + hints + chr(10) if hints else ''}"
        f"賣家：{seller}\n"
        f"連結：{url}"
    )
    return text, plain


def classify_response(status: int | None, payload: dict[str, Any] | None,
                      timed_out: bool = False) -> str:
    if timed_out or status is None:
        return "uncertain"
    if status == 200 and payload and payload.get("ok"):
        return "delivered"
    if status == 400:
        description = str((payload or {}).get("description", "")).lower()
        if any(word in description for word in ("parse", "entit", "html")):
            return "fallback"
        return "permanent"
    if status in (408, 429, 500, 502, 503, 504):
        return "retryable"
    return "permanent"


def _message_id(payload: dict[str, Any] | None) -> int | None:
    if not payload:
        return None
    result = payload.get("result") or {}
    return result.get("message_id")


def send(
    item: dict[str, Any],
    post: Callable[..., Any] | None = None,
    token: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {"status": "dry_run"}

    secret = token or ("injected" if post is not None else None) or _read_token()
    url = f"https://api.telegram.org/bot{secret}/sendMessage"
    post_fn = post or requests.post
    html_text, plain_text = format_item(item)
    payload = {
        "chat_id": CHAT_ID,
        "parse_mode": "HTML",
        "text": html_text,
        "disable_web_page_preview": False,
    }

    for attempt in range(2):
        try:
            response = post_fn(url=url, json=payload, timeout=10)
        except (TimeoutError, requests.exceptions.Timeout):
            return {"status": "uncertain"}
        except Exception as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            return {"status": "retryable", "error": type(exc).__name__}

        data = response.json() if hasattr(response, "json") else {}
        status = classify_response(response.status_code, data)
        if status == "delivered":
            return {"status": "delivered", "message_id": _message_id(data)}

        if status == "fallback":
            plain_payload = dict(payload)
            plain_payload["text"] = plain_text
            plain_payload.pop("parse_mode", None)
            try:
                retry = post_fn(url=url, json=plain_payload, timeout=10)
            except (TimeoutError, requests.exceptions.Timeout):
                return {"status": "uncertain"}
            except Exception as exc:
                return {"status": "retryable", "error": type(exc).__name__}
            retry_data = retry.json() if hasattr(retry, "json") else {}
            retry_status = classify_response(retry.status_code, retry_data)
            if retry_status == "delivered":
                return {"status": "delivered", "message_id": _message_id(retry_data)}
            return {"status": retry_status}

        if status == "retryable" and attempt == 0:
            time.sleep(2)
            continue
        return {"status": status}

    return {"status": "retryable"}


def _read_token() -> str:
    import cm_config
    return cm_config.read_token()
