#!/bin/bash
# 自動重試的補跑排程。
#
# Jev 服務偶爾會 529 過載（實測一天可發生數次），analyze_pending.py 內建的重試
# 撐完仍失敗就會放棄。這支由 cron 每 20 分鐘叫一次，服務恢復時自然就會接上繼續跑，
# 因為 analyze_pending.py 只取 score IS NULL 的資料，處理過的不會重跑。
#
# 防止兩輪重疊（一輪可能要跑 20 分鐘以上）：用 mkdir 當原子鎖，
# macOS 沒有 flock（那是 Linux 的東西），mkdir 在多數檔案系統上是原子操作。
# 全部跑完時推播 Telegram 並自動從 crontab 移除自己。

set -u
BASE="$HOME/CCProject/news-analyzer"
LOG="$HOME/CCProject/logs/jev_backfill.log"
TOKEN_FILE="$HOME/CCProject/.secrets/telegram_token.txt"
CHAT_ID="7556217543"
DAYS=7
LOCKDIR="/tmp/jev-backfill.lockdir"

mkdir -p "$(dirname "$LOG")"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    # 鎖存在但超過 3 小時＝前一次異常中斷留下的殘骸，清掉重來
    if [ -d "$LOCKDIR" ] && [ "$(find "$LOCKDIR" -mmin +180 2>/dev/null)" ]; then
        rmdir "$LOCKDIR" 2>/dev/null
        mkdir "$LOCKDIR" 2>/dev/null || exit 0
    else
        exit 0
    fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

cd "$BASE" || exit 1

remaining_before=$(./venv/bin/python -c "
import sqlite3
c = sqlite3.connect('file:news.db?mode=ro', uri=True)
print(c.execute(\"SELECT COUNT(*) FROM articles WHERE score IS NULL AND fetched_at >= datetime('now','-$DAYS days')\").fetchone()[0])
" 2>/dev/null || echo 0)

echo "=== $(date '+%F %T') 開始，待處理 $remaining_before 筆 ===" >> "$LOG"
[ "$remaining_before" -eq 0 ] && { echo "無待處理，結束" >> "$LOG"; exit 0; }

./venv/bin/python analyze_pending.py --days "$DAYS" >> "$LOG" 2>&1

remaining_after=$(./venv/bin/python -c "
import sqlite3
c = sqlite3.connect('file:news.db?mode=ro', uri=True)
print(c.execute(\"SELECT COUNT(*) FROM articles WHERE score IS NULL AND fetched_at >= datetime('now','-$DAYS days')\").fetchone()[0])
" 2>/dev/null || echo 0)

echo "--- 剩餘 $remaining_after 筆 ---" >> "$LOG"

# 全部完成 → 通知並自我移除排程
if [ "$remaining_after" -eq 0 ]; then
  if [ -f "$TOKEN_FILE" ]; then
    TOKEN=$(cat "$TOKEN_FILE")
    curl -s --max-time 15 -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d "chat_id=${CHAT_ID}" \
      -d "text=✅ Jev 新聞補跑完成（近 ${DAYS} 天）
先前堆積的 14,500+ 筆已全數處理完畢。
詳情見 ~/CCProject/logs/jev_backfill.log" > /dev/null 2>&1
  fi
  # 從 crontab 移除自己
  crontab -l 2>/dev/null | grep -v "run_backfill.sh" | crontab - 2>/dev/null
  echo "=== 完成，已移除排程 ===" >> "$LOG"
fi
