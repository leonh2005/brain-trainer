#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公網 IP 變動偵測 — Shioaji 登入有 IP 白名單，IP 換了沒更新會被拒（detail: ip: x.x.x.x not allow）。

每天開盤前跑一次（cron 08:52），比對上次記錄的公網 IP：
- 不變：安靜結束
- 變了：發 Telegram，提醒去永豐 API 後台更新白名單（gateway 目前用快取 token 仍可運作，
        但 token 一過期重新登入就會失敗）

狀態檔：/tmp/shioaji_ip_watch.json
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

CC = os.path.expanduser("~/CCProject")
TOKEN_FILE = f"{CC}/.secrets/telegram_token.txt"
CHAT_ID = "7556217543"
STATE_FILE = "/tmp/shioaji_ip_watch.json"
GATEWAY = "http://localhost:5455"


def public_ip():
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                ip = r.read().decode().strip()
                if ip and "." in ip and len(ip.split(".")) == 4:
                    return ip
        except Exception:
            continue
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(ip):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"ip": ip, "ts": datetime.now().isoformat()}, f)
    except Exception:
        pass


def send(text):
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
        )
    except Exception as e:
        print(f"[telegram] 發送失敗: {e}")


def gateway_state():
    try:
        with urllib.request.urlopen(f"{GATEWAY}/health", timeout=8) as r:
            h = json.loads(r.read())
        return (f"state={h.get('state')} gen={h.get('generation')} "
                f"logged_in={h.get('logged_in')}")
    except Exception as e:
        return f"無法取得: {e}"


def main():
    dry = "--dry" in sys.argv
    ip = public_ip()
    if not ip:
        print("無法取得公網 IP，跳過")
        return
    state = load_state()
    prev = state.get("ip")
    if not dry:
        save_state(ip)
    if prev and prev != ip:
        text = (
            "⚠️ Shioaji 公網 IP 變更\n"
            f"{prev} → {ip}\n"
            "請確認永豐 API 白名單已更新，否則 token 過期後重新登入會被拒（ip not allow）。\n"
            f"gateway: {gateway_state()}"
        )
        print(text)
        if not dry:
            send(text)
    elif not prev:
        print(f"首次記錄公網 IP: {ip}（gateway: {gateway_state()}）")
        # 首次記錄也發一次，讓使用者知道白名單上的 IP 應該是這個
        if not dry:
            send(f"ℹ️ Shioaji 公網 IP 已記錄: {ip}\n請確認永豐 API 白名單包含此 IP。\n"
                 f"gateway: {gateway_state()}")
    else:
        print(f"公網 IP 未變: {ip}")


if __name__ == "__main__":
    main()
