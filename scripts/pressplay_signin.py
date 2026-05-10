#!/usr/bin/env python3
import sqlite3, shutil, requests, re, sys
from datetime import datetime

FF_COOKIES_DB = "/Users/steven/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite"

def get_cookies():
    tmp = "/tmp/pressplay_cookies.sqlite"
    shutil.copy2(FF_COOKIES_DB, tmp)
    conn = sqlite3.connect(tmp)
    rows = conn.execute("SELECT name, value, host FROM moz_cookies WHERE host LIKE '%pressplay%'").fetchall()
    conn.close()
    return rows

rows = get_cookies()
session = requests.Session()
access_token = ""
for name, value, host in rows:
    session.cookies.set(name, value, domain=host.lstrip("."))
    if name == "JAccessToken":
        access_token = value

if not access_token:
    print("❌ 找不到 JAccessToken，請先用 Firefox 登入 pressplay.cc")
    sys.exit(1)

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Authorization": f"Bearer {access_token}",
    "PP-APP-VER": "1.0",
    "PP-OS": "1.0",
    "PP-OS-VER": "1.0",
    "PP-DEVICE-ID": "howdoyouturnthison",
    "PP-TIMEZONE-OFFSET": "-480",
    "PP-TIMEZONE": "Asia/Taipei",
    "PP-LOCALE": "zh-TW",
    "PP-REGION": "TW",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.pressplay.cc/",
    "Origin": "https://www.pressplay.cc",
}

r = session.post("https://api-web.pressplay.cc/member/week_checkin", headers=headers, json={})
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if r.status_code == 200:
    data = r.json()
    if data.get("status") == "success":
        points = data.get("data", {}).get("points", "?")
        print(f"[{now}] ✅ PressPlay 簽到成功，獲得 {points} 點")
    else:
        print(f"[{now}] ⚠️ 已簽到或其他狀態: {r.text[:100]}")
else:
    print(f"[{now}] ❌ 簽到失敗 ({r.status_code}): {r.text[:100]}")
    sys.exit(1)
