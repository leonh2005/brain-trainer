#!/bin/bash
# Shopee 軟纖提摩西庫存監控

export HOME=/Users/steven
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export DISPLAY=

cd /Users/steven/CCProject/daytrade-replay
source venv/bin/activate

RESULT=$(python3 /Users/steven/CCProject/shopee_stock_check.py 2>>/Users/steven/CCProject/logs/shopee_stock.log)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $RESULT" >> /Users/steven/CCProject/logs/shopee_stock.log

# 訂單成立後移除排程
if echo "$RESULT" | grep -qE "ORDER_PLACED|ORDER_UNCERTAIN"; then
    crontab -l 2>/dev/null | grep -v 'shopee_stock_cron' | crontab -
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] crontab 已移除（訂單完成）" >> /Users/steven/CCProject/logs/shopee_stock.log
fi
