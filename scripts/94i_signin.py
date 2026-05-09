#!/usr/bin/env python3
import requests
import re
import sys
from datetime import datetime

USERNAME = "neolh"
PASSWORD = "hmily1yk"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
})

# 取得登入頁 formhash 和 cookies
r = session.get("https://94i.in/member.php?mod=logging&action=login")
formhash = re.search(r'name="formhash" value="([^"]+)"', r.text)
if not formhash:
    print("❌ 無法取得 formhash")
    sys.exit(1)
formhash = formhash.group(1)

# 登入
r = session.post(
    "https://94i.in/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes&inajax=1",
    data={
        "formhash": formhash,
        "referer": "https://94i.in/",
        "loginfield": "username",
        "username": USERNAME,
        "password": PASSWORD,
        "questionid": "0",
        "answer": "",
        "cookietime": "2592000",
    },
)

uid_match = re.search(r"discuz_uid = '(\d+)'", session.get("https://94i.in/").text)
if not uid_match or uid_match.group(1) == "0":
    print("❌ 登入失敗")
    sys.exit(1)

# 簽到（訪問簽到頁面即觸發）
r = session.get("https://94i.in/plugin.php?id=dsu_amupper:sign")

last_sign = re.search(r"上次簽到.*?<span[^>]*>(.*?)</span>", r.text)
total = re.search(r"累計簽到.*?<span[^>]*>(\d+)</span>", r.text)
streak = re.search(r"連續簽到.*?<span[^>]*>(\d+)</span>", r.text)

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print(f"[{now}] ✅ 94i 簽到成功")
if total:
    print(f"  累計: {total.group(1)} 次 | 連續: {streak.group(1) if streak else '?'} 次")
if last_sign:
    print(f"  上次簽到: {last_sign.group(1)}")
