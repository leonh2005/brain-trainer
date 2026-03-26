#!/bin/bash
# 每日首次登入狀態檢查 — 只執行一次（用日期 flag 控制）

FLAG="/tmp/claude_daily_check_$(date +%Y-%m-%d)"
[ -f "$FLAG" ] && exit 0
touch "$FLAG"

PASS="✅" FAIL="❌" WARN="⚠️"
OUT=""

# 1. rabbit-care (port 5200)
if lsof -i :5200 2>/dev/null | grep -q LISTEN; then
  OUT+="$PASS rabbit-care (port 5200)\n"
else
  OUT+="$FAIL rabbit-care (port 5200) — 未啟動\n"
fi

# 2. VM 服務
VM_STATUS=$(ssh -i ~/.ssh/oracle_line_bot -o ConnectTimeout=6 -o BatchMode=yes ubuntu@161.33.6.190 \
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
