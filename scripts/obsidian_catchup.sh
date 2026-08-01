#!/bin/bash
# 一次性補跑：把 Raw/ 裡累積的舊 Google Keep 筆記全部 ingest 完（迴圈直到沒有新文章為止）
# 由 crontab 一次性排程於 2026-08-02 00:00 觸發，執行完會自我移除該筆 crontab

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

VAULT="/Volumes/1TOWC/Obsidian"
TELEGRAM_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
TELEGRAM_CHAT_ID="7556217543"
LOG="$HOME/CCProject/logs/obsidian_catchup.log"
MAX_ROUNDS=15

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
send_telegram() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" > /dev/null 2>&1
}

# 執行完（不論成功失敗）都把自己從 crontab 移除，避免明年同一天又觸發一次
cleanup_cron() {
  crontab -l 2>/dev/null | grep -v "obsidian_catchup.sh" | crontab -
  log "已從 crontab 移除一次性補跑任務"
}
trap cleanup_cron EXIT

log "=== 開始一次性補跑 Raw/ ingest ==="
send_telegram "🌱 Obsidian 一次性補跑開始，會分批處理直到全部完成（可能要一段時間）"

round=0
while [ "$round" -lt "$MAX_ROUNDS" ]; do
  round=$((round+1))
  log "--- 第 $round 輪 ---"

  prompt="請照這個 vault 根目錄的 claude.md 說明操作。掃描 Raw/ 資料夾，找出還沒被 Wiki/Sources/ 對應頁面記錄過的文章，這次挑其中一批（例如 10-15 篇）照 claude.md 的流程整理進 Wiki（建/更新 Concepts、Entities、Sources 頁面），更新 Wiki/Index.md，並在 Log/ 寫紀錄。不用一次處理完全部，處理一批高品質的即可，我會重複呼叫你直到全部處理完。

【安全規則】
- 只能讀寫這個 vault 目錄（$VAULT）底下的檔案
- 不要刪除或修改 Raw/ 裡的原始檔案，只能讀取
- 這是無人值守自動執行，不會有人跟你互動確認

完成後用繁體中文簡短總結：這一批處理了幾篇、還剩幾篇沒處理。如果 Raw 裡已經沒有任何新文章要 ingest 了，回覆裡務必包含關鍵字 NOTHING_NEW。"

  result=$(cd "$VAULT" && timeout --kill-after=10 2700 claude -p "$prompt" \
    --permission-mode acceptEdits \
    --allowedTools "Read,Write,Edit,Bash,Grep,Glob" \
    --disallowedTools "Agent,Workflow" \
    --output-format text 2>>"$LOG")
  exit_code=$?

  log "第 $round 輪結果 (exit=$exit_code)：$result"

  if [ "$exit_code" -ne 0 ]; then
    send_telegram "⚠️ Obsidian 補跑第 $round 輪失敗 (exit=$exit_code)，詳見 $LOG，停止後續輪次"
    exit 1
  fi

  if echo "$result" | grep -q "NOTHING_NEW"; then
    log "偵測到 NOTHING_NEW，全部處理完成"
    send_telegram "✅ Obsidian 一次性補跑全部完成，共跑了 $round 輪：
$result"
    exit 0
  fi
done

log "達到最大輪數 $MAX_ROUNDS 仍未完成，停止並通知"
send_telegram "⚠️ Obsidian 補跑跑了 $MAX_ROUNDS 輪還沒處理完，可能需要再手動跑一次，詳見 $LOG"
