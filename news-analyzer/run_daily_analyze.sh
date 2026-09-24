#!/bin/bash
# 常態分析：Jev 篩相關性 → 相關的才送 LLM。
#
# 與 run_backfill.sh 的差別：
#   run_backfill.sh 是一次性補跑（跑完自我移除、會通知）
#   這支是常態排程（永久留在 crontab、只在異常時通知，避免洗頻）
#
# 每 15 分鐘跑一次，比 pipeline 的抓取晚 5 分鐘，讓新文章先落庫。

set -u
BASE="$HOME/CCProject/news-analyzer"
LOG="$HOME/CCProject/logs/jev_daily_analyze.log"
TOKEN_FILE="$HOME/CCProject/.secrets/telegram_token.txt"
CHAT_ID="7556217543"
LOCKDIR="/tmp/jev-daily-analyze.lockdir"
DAYS=3          # 只處理近 3 天（更舊的由補跑腳本處理）
LIMIT=400       # 單次上限，避免積壓時卡太久

mkdir -p "$(dirname "$LOG")"

# mkdir 原子鎖（macOS 沒有 flock）
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    if [ -d "$LOCKDIR" ] && [ "$(find "$LOCKDIR" -mmin +60 2>/dev/null)" ]; then
        rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || exit 0
    else
        exit 0
    fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

cd "$BASE" || exit 1

out=$(./venv/bin/python analyze_pending.py --days "$DAYS" --limit "$LIMIT" 2>&1)
code=$?
echo "=== $(date '+%F %T') ===" >> "$LOG"
echo "$out" | grep -vE "HTTP Request" >> "$LOG"

pending=$(./venv/bin/python -c "
import sqlite3
c = sqlite3.connect('file:news.db?mode=ro', uri=True)
print(c.execute(\"SELECT COUNT(*) FROM articles WHERE score IS NULL AND fetched_at >= datetime('now','-$DAYS days')\").fetchone()[0])
" 2>/dev/null || echo 0)

# 只在異常時通知（正常跑不打擾）
if [ "$code" -ne 0 ]; then
    [ -f "$TOKEN_FILE" ] && curl -s --max-time 15 -X POST \
      "https://api.telegram.org/bot$(cat "$TOKEN_FILE")/sendMessage" \
      -d "chat_id=${CHAT_ID}" \
      -d "text=⚠️ 新聞分析排程異常（exit $code）\n剩餘 $pending 筆待處理\n詳見 $LOG" > /dev/null 2>&1
elif [ "$pending" -gt 1000 ]; then
    # 積壓過多＝跟不上，提醒一下
    [ -f "$TOKEN_FILE" ] && curl -s --max-time 15 -X POST \
      "https://api.telegram.org/bot$(cat "$TOKEN_FILE")/sendMessage" \
      -d "chat_id=${CHAT_ID}" \
      -d "text=⚠️ 新聞分析積壓 $pending 筆（近 ${DAYS} 天），可能 Jev 或 LLM 有狀況" > /dev/null 2>&1
fi
