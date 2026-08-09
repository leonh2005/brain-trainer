#!/bin/bash
# 啟動 Cloudflare Quick Tunnel 並將 URL 傳送至 Telegram

BOT_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
CHAT_ID="7556217543"
LOG="/Users/steven/CCProject/command-center/logs/tunnel.log"

echo "[$(date)] 啟動 tunnel..." >> "$LOG"

sleep 5

/opt/homebrew/bin/cloudflared tunnel --protocol http2 --url http://localhost:5950 --no-autoupdate 2>&1 | while IFS= read -r line; do
    echo "[$(date)] $line" >> "$LOG"
    if [[ "$line" == *"trycloudflare.com"* ]]; then
        URL=$(echo "$line" | grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com')
        if [ -n "$URL" ]; then
            curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
                -d chat_id="$CHAT_ID" \
                -d text="🖥️ AI 指揮中心外部連結：
${URL}

帳號：steven
密碼：Vkgm1IRPvQ0rSS8e" >> "$LOG" 2>&1
            echo "[$(date)] URL 已傳送：$URL" >> "$LOG"
        fi
    fi
done
