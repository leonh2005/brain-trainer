#!/bin/bash
# 每天維持蝦皮登入 session（避免自動登出）
export HOME=/Users/steven
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin

cd /Users/steven/CCProject/daytrade-replay
source venv/bin/activate

python3 - << 'EOF'
import os, time
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.by import By

PROFILE = os.path.expanduser('~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release')
opts = Options()
opts.add_argument('--headless')
opts.profile = PROFILE
driver = webdriver.Firefox(options=opts)
try:
    driver.get('https://shopee.tw')
    time.sleep(6)
    # 確認是否登入
    body = driver.find_element(By.TAG_NAME, 'body').text
    logged_in = 'stevenhung' in body or '登入' not in body[:200]
    print('SESSION_OK' if logged_in else 'SESSION_EXPIRED')
finally:
    driver.quit()
EOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] keepalive done"
