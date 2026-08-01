#!/bin/bash
# 每天早上 7 點掃 Obsidian Vault 的 Raw/，把新文章 ingest 進 Wiki（Karpathy 免 RAG 筆記法）
# 由 crontab 執行（0 7 * * *）

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

VAULT="/Volumes/1TOWC/Obsidian"
TELEGRAM_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
TELEGRAM_CHAT_ID="7556217543"
LOG="$HOME/CCProject/logs/obsidian_ingest.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

send_telegram() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" > /dev/null 2>&1
}

if [ ! -d "$VAULT" ]; then
  log "外接硬碟 1TOWC 未掛載，跳過本次 ingest"
  send_telegram "🌱 Obsidian ingest 跳過：外接硬碟 1TOWC 未插上，請確認"
  exit 0
fi

raw_count=$(find "$VAULT/Raw" -maxdepth 1 -type f \( -iname "*.md" -o -iname "*.txt" \) 2>/dev/null | wc -l | tr -d ' ')
if [ "$raw_count" -eq 0 ]; then
  log "Raw/ 目前沒有檔案，跳過本次 ingest"
  exit 0
fi

prompt="請照這個 vault 根目錄的 claude.md 說明操作。掃描 Raw/ 資料夾，找出還沒被 Wiki/Sources/ 對應頁面記錄過的文章，照 claude.md 的流程整理進 Wiki（建/更新 Concepts、Entities、Sources 頁面），更新 Wiki/Index.md，並在 Log/ 寫今天的紀錄。

【安全規則】
- 只能讀寫這個 vault 目錄（$VAULT）底下的檔案，不要碰其他地方
- 不要刪除或修改 Raw/ 裡的原始檔案，只能讀取
- 這是無人值守自動執行，不會有人跟你互動確認

完成後用繁體中文簡短總結（3行內）：處理了幾篇文章、新增了哪些 Wiki 頁面；如果 Raw 裡沒有新文章要 ingest，就直接說「今天沒有新文章」。"

result=$(cd "$VAULT" && timeout --kill-after=10 3600 claude -p "$prompt" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Write,Edit,Bash,Grep,Glob" \
  --disallowedTools "Agent,Workflow" \
  --output-format text 2>>"$LOG")
exit_code=$?

log "ingest 結果 (exit=$exit_code)：$result"

if [ "$exit_code" -eq 0 ] && [ -n "$result" ]; then
  send_telegram "🌱 Obsidian 每日 ingest 完成：
$result"
else
  send_telegram "⚠️ Obsidian ingest 執行失敗 (exit=$exit_code)，詳見 $LOG"
fi
