#!/bin/bash
# 半夜服務狀態檢查 — 純 bash，只在發現問題時才呼叫 claude

BOT_TOKEN="8666778924:AAFMAFKfsfx3opG7MIx6vcJKACTk"
CHAT_ID="7556217543"
SSH="ssh -i ~/.ssh/oracle_line_bot -o ConnectTimeout=10 -o BatchMode=yes ubuntu@161.33.6.190"
CLAUDE=/Users/steven/.local/bin/claude
LOG_DIR=~/CCProject/logs
BOT_TOKEN="8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"

send_telegram() {
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        --data-urlencode "text=$1" > /dev/null
}

PASS="✅" FAIL="❌"
FIXES=""
REPORT="【半夜巡邏報告】$(date '+%Y-%m-%d %H:%M')\n\n[Mac 服務]"

# ── 1. rabbit-care ──────────────────────────────
if lsof -i :5200 2>/dev/null | grep -q LISTEN; then
    REPORT+="\n$PASS rabbit-care (port 5200)"
else
    REPORT+="\n$FAIL rabbit-care (port 5200)"
    launchctl load ~/Library/LaunchAgents/com.steven.rabbit-care.plist 2>/dev/null
    sleep 3
    if lsof -i :5200 2>/dev/null | grep -q LISTEN; then
        FIXES+="\n- rabbit-care 自動重啟成功"
    else
        FIXES+="\n- rabbit-care 重啟失敗，需人工處理"
    fi
fi

# ── 2. stock-screener ───────────────────────────
if lsof -i :5001 2>/dev/null | grep -q LISTEN; then
    REPORT+="\n$PASS stock-screener (port 5001)"
else
    REPORT+="\n$FAIL stock-screener (port 5001)"
    launchctl load ~/Library/LaunchAgents/com.steven.stock-screener.plist 2>/dev/null
    FIXES+="\n- stock-screener 嘗試重啟"
fi

# ── 3. youtube-monitor ──────────────────────────
if pgrep -f youtube_monitor.py > /dev/null; then
    REPORT+="\n$PASS youtube-monitor"
else
    REPORT+="\n$FAIL youtube-monitor"
    cd ~/youtube-monitor && nohup python3 youtube_monitor.py >> ~/youtube-monitor/monitor.log 2>&1 &
    FIXES+="\n- youtube-monitor 自動重啟"
fi

# ── 4. 今日摘要數 ────────────────────────────────
SUMMARY_COUNT=$(ls ~/youtube-monitor/summaries/$(date +%Y-%m-%d)/*.txt 2>/dev/null | grep -v transcript | wc -l | tr -d ' ')
REPORT+="\n📄 今日摘要：${SUMMARY_COUNT} 支"

# ── 5. Oracle VM ────────────────────────────────
REPORT+="\n\n[Oracle VM]"

VM_OUT=$($SSH "df -h / | tail -1; free -h | grep Mem; systemctl is-active tele-bot.service; systemctl is-active stock-screener.service; exit 0" 2>&1)

if [ $? -ne 0 ]; then
    REPORT+="\n$FAIL 連線失敗"
else
    REPORT+="\n$PASS 連線正常"

    # 磁碟
    DISK_PCT=$(echo "$VM_OUT" | head -1 | awk '{print $5}' | tr -d '%')
    DISK_DISP=$(echo "$VM_OUT" | head -1 | awk '{print $5}')
    if [ -n "$DISK_PCT" ] && [ "$DISK_PCT" -gt 85 ] 2>/dev/null; then
        $SSH "sudo journalctl --vacuum-size=100M" > /dev/null 2>&1
        REPORT+="\n⚠️ 磁碟：${DISK_DISP} 使用（已自動清理 journal）"
        FIXES+="\n- VM 磁碟 ${DISK_DISP}，清理 journalctl"
    else
        REPORT+="\n💾 磁碟：${DISK_DISP:-N/A} 使用"
    fi

    # 記憶體
    MEM_AVAIL=$(echo "$VM_OUT" | grep Mem | awk '{print $7}')
    REPORT+="\n🧠 記憶體：可用 ${MEM_AVAIL:-N/A}"

    # tele-bot
    TELE_STATUS=$(echo "$VM_OUT" | tail -2 | head -1)
    if [ "$TELE_STATUS" = "active" ]; then
        REPORT+="\n$PASS tele-bot"
    else
        REPORT+="\n$FAIL tele-bot"
        $SSH "sudo systemctl restart tele-bot.service" > /dev/null 2>&1
        sleep 10
        NEW_STATUS=$($SSH "systemctl is-active tele-bot.service" 2>/dev/null)
        if [ "$NEW_STATUS" = "active" ]; then
            FIXES+="\n- tele-bot 自動重啟成功"
        else
            FIXES+="\n- tele-bot 重啟失敗，需人工處理"
        fi
    fi

    # stock-screener (VM)
    SS_STATUS=$(echo "$VM_OUT" | tail -1)
    if [ "$SS_STATUS" = "active" ]; then
        REPORT+="\n$PASS stock-screener (VM)"
    else
        REPORT+="\n$FAIL stock-screener (VM)"
        $SSH "sudo systemctl restart stock-screener.service" > /dev/null 2>&1
        FIXES+="\n- VM stock-screener 嘗試重啟"
    fi
fi

# ── 6. 修復摘要 ─────────────────────────────────
REPORT+="\n\n[修復動作]"
if [ -z "$FIXES" ]; then
    REPORT+="\n無需修復"
else
    REPORT+="$FIXES"
fi

# ── 輸出 & 推播 ──────────────────────────────────
echo -e "$REPORT" | tee -a "$LOG_DIR/nightly_check.log"
send_telegram "$(echo -e "$REPORT")"
