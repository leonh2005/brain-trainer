#!/bin/bash
# 每日 log 掃描腳本：檢查所有監測任務過去 24 小時的錯誤並推播 Telegram

TG_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
TG_CHAT="7556217543"
SSH_KEY="$HOME/.ssh/oracle_line_bot"
VM_HOST="ubuntu@161.33.6.190"

# 本機 log 清單（服務名稱:路徑）
LOCAL_LOGS=(
    "news-analyzer:$HOME/CCProject/news-analyzer/pipeline.log"
    "shopee監測:$HOME/CCProject/logs/shopee_stock.log"
    "daily-stock-analysis:$HOME/CCProject/daily-stock-analysis/webui.log"
    "rabbit-care:$HOME/CCProject/rabbit-care/rabbit-care.log"
    "motion-watcher:$HOME/CCProject/rabbit-care/motion-watcher.log"
    "banini-tracker:$HOME/CCProject/banini-tracker/banini.log"
    "daytrade-replay:$HOME/CCProject/daytrade-replay/server.log"
    "kelly-fibonacci:$HOME/CCProject/kelly-fibonacci/server.log"
    "stock-screener-ai:$HOME/CCProject/stock-screener-ai/screener-ai.log"
    "threads-daily:$HOME/CCProject/threads-daily/cron.log"
    "youtube-monitor:$HOME/youtube-monitor/monitor.log"
    "bd-monitor:$HOME/youtube-monitor/logs/bd_monitor.log"
    "claude-cycle-monitor:$HOME/CCProject/claude_cycle_monitor.log"
    "market-dashboard:$HOME/CCProject/logs/market_dashboard.log"
    "vol-rank-updater:$HOME/CCProject/logs/vol_rank_updater.log"
    "intraday-monitor:$HOME/CCProject/logs/intraday_monitor.log"
    "daytrade-alert:$HOME/CCProject/logs/daytrade.log"
    "screener:$HOME/CCProject/logs/screener.log"
    "nightly-check:$HOME/CCProject/logs/nightly_check.log"
    "dashboard:$HOME/CCProject/dashboard/dashboard.log"
)

# VM log 清單
VM_LOGS=(
    "telebot:$HOME/telebot/logs/tele-bot.log"
    "stock-screener-vm:/home/ubuntu/stock-screener/shioaji.log"
)

# 已知可忽略的雜訊模式
IGNORE_PATTERNS=(
    "3260.TW.*possibly delisted"
    "Quote not found for symbol: [0-9]*.TW"
    "\" 404 -$"  # werkzeug access log 的404（機器人亂掃路徑，如 /exception.log、/_layouts/*/error.aspx），不是應用程式錯誤
    "No data found, symbol may be delisted"
    "GET /.env HTTP"
    "UserWarning"
)

tg_send() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT}" \
        --data-urlencode "text=${msg}" > /dev/null
}

scan_log() {
    local name="$1"
    local path="$2"
    local content="$3"  # 空則從本機讀

    if [ -z "$content" ]; then
        [ ! -f "$path" ] && return
        # 只取過去 24 小時的內容
        content=$(find "$path" -newer /tmp/_daily_log_check_anchor 2>/dev/null | xargs grep -hiE "ERROR|CRITICAL|Traceback|Exception|failed|timed out" 2>/dev/null | head -20)
    else
        content=$(echo "$content" | grep -iE "ERROR|CRITICAL|Traceback|Exception|failed|timed out" | head -20)
    fi

    [ -z "$content" ] && return

    # 過濾已知雜訊
    for pattern in "${IGNORE_PATTERNS[@]}"; do
        content=$(echo "$content" | grep -v "$pattern")
    done

    [ -z "$content" ] && return

    echo "$name"
}

# 建立時間錨點（24 小時前）
touch -d "24 hours ago" /tmp/_daily_log_check_anchor 2>/dev/null || \
    touch -t "$(date -v-24H '+%Y%m%d%H%M')" /tmp/_daily_log_check_anchor 2>/dev/null

ERRORS=""

# 掃本機 log
for entry in "${LOCAL_LOGS[@]}"; do
    name="${entry%%:*}"
    path="${entry#*:}"
    path="${path/#\~/$HOME}"

    [ ! -f "$path" ] && continue

    hits=$(find "$path" -newer /tmp/_daily_log_check_anchor 2>/dev/null | xargs grep -hiE "ERROR|CRITICAL|Traceback|Exception|failed|timed out" 2>/dev/null)
    [ -z "$hits" ] && continue

    # 過濾雜訊
    for pattern in "${IGNORE_PATTERNS[@]}"; do
        hits=$(echo "$hits" | grep -v "$pattern")
    done
    [ -z "$hits" ] && continue

    sample=$(echo "$hits" | tail -3 | sed 's/^/  /')
    ERRORS="${ERRORS}⚠️ ${name}\n${sample}\n\n"
done

# 掃 VM log
vm_content=$(ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$VM_HOST" "
    for f in /home/ubuntu/telebot/logs/tele-bot.log /home/ubuntu/stock-screener/shioaji.log; do
        [ -f \"\$f\" ] || continue
        label=\$(basename \$f .log)
        hits=\$(awk -v d=\"\$(date -d '24 hours ago' '+%Y-%m-%d %H:%M' 2>/dev/null || date -v-24H '+%Y-%m-%d %H:%M')\" '\$0 >= d' \"\$f\" 2>/dev/null | grep -iE 'ERROR|CRITICAL|Traceback|Exception|failed|timed out' | tail -3)
        [ -n \"\$hits\" ] && echo \"VM:\$label\" && echo \"\$hits\"
    done
" 2>/dev/null)

if [ -n "$vm_content" ]; then
    ERRORS="${ERRORS}⚠️ VM\n$(echo "$vm_content" | sed 's/^/  /' | head -9)\n\n"
fi

# 推播
if [ -n "$ERRORS" ]; then
    msg="🔍 每日 Log 掃描報告（$(date '+%Y-%m-%d')）\n\n${ERRORS}請確認並修復。"
    tg_send "$msg"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 發現錯誤，已推播 Telegram"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 所有 log 正常，無錯誤"
fi

rm -f /tmp/_daily_log_check_anchor
