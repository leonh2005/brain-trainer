#!/bin/bash
# 每日首次登入狀態檢查 — 只執行一次（用日期 flag 控制）

FLAG="/tmp/claude_daily_check_$(date +%Y-%m-%d)"
[ -f "$FLAG" ] && exit 0
touch "$FLAG"

PASS="✅" FAIL="❌" WARN="⚠️"
OUT=""

# 1. 本地 Web 服務
declare -A SERVICES=(
  [3099]="banini-tracker"
  [5001]="stock-screener"
  [5173]="dsa-vite"
  [5200]="rabbit-care"
  [5300]="news-analyzer"
  [5400]="daytrade-replay"
  [5500]="stock-screener-ai"
  [5550]="timesfm"
  [5600]="dashboard"
  [5650]="dsa-webui"
  [5700]="kelly-fibonacci"
  [8000]="dsa-backend"
)
declare -A OPTIONAL_SERVICES=(
  [5050]="ai-compare"
  [5100]="stock_analyzer"
  [5800]="portfolio-analyzer"
)

for port in $(echo "${!SERVICES[@]}" | tr ' ' '\n' | sort -n); do
  name="${SERVICES[$port]}"
  if lsof -i :$port 2>/dev/null | grep -q LISTEN; then
    OUT+="$PASS $name (port $port)\n"
  else
    OUT+="$FAIL $name (port $port) — 未啟動\n"
  fi
done

for port in $(echo "${!OPTIONAL_SERVICES[@]}" | tr ' ' '\n' | sort -n); do
  name="${OPTIONAL_SERVICES[$port]}"
  if lsof -i :$port 2>/dev/null | grep -q LISTEN; then
    OUT+="$PASS $name (port $port)\n"
  else
    OUT+="$WARN $name (port $port) — 停止中\n"
  fi
done

# 2. VM 服務
VM_STATUS=$(ssh -i ~/.ssh/oracle_line_bot -o ConnectTimeout=6 -o BatchMode=yes \
  -o ServerAliveInterval=3 -o ServerAliveCountMax=2 ubuntu@161.33.6.190 \
  "ps aux | grep -v grep" 2>/dev/null)

if echo "$VM_STATUS" | grep -q "/telebot/"; then
  OUT+="$PASS telebot (Oracle VM)\n"
else
  OUT+="$FAIL telebot (Oracle VM) — 進程不在\n"
fi

if echo "$VM_STATUS" | grep -q "/stock-screener/"; then
  OUT+="$PASS stock-screener (Oracle VM)\n"
else
  OUT+="$FAIL stock-screener (Oracle VM) — 進程不在\n"
fi

# 3. Mac cron：voice_ideas_report 今天有沒有跑
TODAY=$(date +%Y-%m-%d)
LOG="/Users/steven/CCProject/logs/voice_ideas_report.log"
if grep -q "$TODAY" "$LOG" 2>/dev/null; then
  OUT+="$PASS voice_ideas_report (今天已執行)\n"
else
  OUT+="$WARN voice_ideas_report (今天尚未執行或無 log)\n"
fi

# 4. thread_summarizer 最後一筆日期
LAST=$(tail -1 /Users/steven/CCProject/logs/thread_summarizer.log 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)
YESTERDAY=$(date -v-1d +%Y-%m-%d)
if [ "$LAST" = "$TODAY" ] || [ "$LAST" = "$YESTERDAY" ]; then
  OUT+="$PASS thread_summarizer (最後：$LAST)\n"
else
  OUT+="$WARN thread_summarizer (最後：${LAST:-無記錄}，請確認)\n"
fi

echo ""
echo "╔══════════════════════════════╗"
echo "║  每日服務狀態報告 $(date +%m/%d)       ║"
echo "╚══════════════════════════════╝"
printf "$OUT"
echo ""
