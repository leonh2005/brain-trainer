#!/bin/bash
# 啟動 Cloudflare Quick Tunnel 並將 URL 傳送至 Telegram

BOT_TOKEN="8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID="7556217543"
LOG="/Users/steven/CCProject/rabbit-care/tunnel.log"

echo "[$(date)] 啟動 tunnel..." >> "$LOG"

# 等待 Flask 準備好
sleep 5

# 啟動 cloudflared，擷取 URL
/opt/homebrew/bin/cloudflared tunnel --url http://localhost:5200 --no-autoupdate 2>&1 | while IFS= read -r line; do
    echo "[$(date)] $line" >> "$LOG"
    if [[ "$line" == *"trycloudflare.com"* ]]; then
        URL=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com')
        if [ -n "$URL" ]; then
            curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
                -d chat_id="$CHAT_ID" \
                -d text="🐰 兔子照護系統外部連結：
${URL}

密碼：momo2026" >> "$LOG" 2>&1
            echo "[$(date)] URL 已傳送：$URL" >> "$LOG"
        fi
    fi
done
