#!/bin/bash
# 半夜服務狀態檢查 + VM 自動修復 — 用 claude -p 執行並推播 Telegram

BOT_TOKEN="8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID="7556217543"

send_telegram() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        --data-urlencode "text=$1" > /dev/null
}

send_telegram "🌙 開始半夜服務檢查 + VM 健康巡邏..."

REPORT=$(/Users/steven/.local/bin/claude --dangerously-skip-permissions -p "
你是 Steven 的系統維護助手。請依序執行以下工作，發現問題就直接修復，不要只報告。

## 一、Mac 本地服務檢查

1. rabbit-care：執行 lsof -i :5200 | grep LISTEN
2. stock-screener：執行 lsof -i :5001 | grep LISTEN
3. youtube-monitor：執行 pgrep -f youtube_monitor.py
4. 今日摘要數：執行 ls ~/youtube-monitor/summaries/\$(date +%Y-%m-%d)/*.txt 2>/dev/null | grep -v transcript | wc -l

若服務沒在跑：
- rabbit-care 掛了：執行 launchctl load ~/Library/LaunchAgents/com.steven.rabbit-care.plist
- stock-screener 掛了：執行 launchctl load ~/Library/LaunchAgents/com.steven.stock-screener.plist
- youtube-monitor 掛了：執行 cd ~/youtube-monitor && nohup python3 youtube_monitor.py &

## 二、Oracle VM 檢查與修復

SSH 指令前綴：ssh -i ~/.ssh/oracle_line_bot -o ConnectTimeout=10 -o BatchMode=yes ubuntu@161.33.6.190

1. 連線測試：執行 echo ok
2. 磁碟空間：執行 df -h / | tail -1（若使用率 > 85% 則清理 journalctl：sudo journalctl --vacuum-size=100M）
3. 記憶體：執行 free -h | grep Mem
4. tele-bot 服務：執行 systemctl is-active tele-bot.service（若非 active，執行 sudo systemctl restart tele-bot.service 並等待 10 秒確認）
5. stock-screener 服務：執行 systemctl is-active stock-screener.service（若非 active，嘗試重啟）
6. 檢查是否有重複服務衝突：執行 systemctl list-units --type=service --state=failed | grep -v LOAD
7. journalctl 近期錯誤：執行 journalctl -p err -n 5 --no-pager（列出最近 5 條 error 等級以上的 log）

若發現任何 failed service，直接執行 sudo systemctl restart <service名稱>。

## 三、輸出報告

最後輸出純文字報告（不要用 markdown 符號 * 或 #），格式如下：

【半夜巡邏報告】YYYY-MM-DD HH:MM

[Mac 服務]
✅/❌ rabbit-care (port 5200)
✅/❌ stock-screener (port 5001)
✅/❌ youtube-monitor
📄 今日摘要：N 支

[Oracle VM]
✅/❌ 連線
💾 磁碟：XX% 使用
🧠 記憶體：可用 XXG
✅/❌ tele-bot
✅/❌ stock-screener

[修復動作]
（列出本次自動修復了什麼，若無則寫「無需修復」）
" 2>&1)

if [ -z "$REPORT" ]; then
    send_telegram "❌ 半夜巡邏失敗：claude 無回應"
else
    send_telegram "$REPORT"
fi
