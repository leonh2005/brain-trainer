#!/bin/bash
# Shopee 軟纖提摩西庫存監控
# 有貨時自動加入購物車並傳 Telegram 通知

export HOME=/Users/steven
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export DISPLAY=

cd /Users/steven/CCProject/daytrade-replay
source venv/bin/activate

RESULT=$(python3 /Users/steven/CCProject/shopee_stock_check.py 2>/dev/null)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $RESULT"

# 有貨時移除本 crontab 排程（停止監控）
if echo "$RESULT" | grep -q "TELEGRAM_SENT"; then
    # 把 shopee 這行從 crontab 移除
    crontab -l 2>/dev/null | grep -v 'shopee_stock_cron' | crontab -
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] crontab 已移除"
fi
