#!/bin/bash
# 每天巡邏 Google 雲端硬碟根目錄，把誤放在最外層的散檔歸位到既有6大分類資料夾，
# 判斷不出來的丟進「待整理」（Steven 既有的人工暫存慣例）
# 由 crontab 執行（每天早上，跟 obsidian_ingest.sh 錯開）

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

DRIVE="$HOME/我的雲端硬碟"
TELEGRAM_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
TELEGRAM_CHAT_ID="7556217543"
LOG="$HOME/CCProject/logs/gdrive_sort.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
send_telegram() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" > /dev/null 2>&1
}

if [ ! -d "$DRIVE" ]; then
  log "雲端硬碟未掛載，跳過"
  exit 0
fi

# 只抓根目錄「檔案」（不含資料夾），排除系統隱藏檔
loose_files=$(find "$DRIVE" -maxdepth 1 -type f ! -name ".*" ! -name "Icon" 2>/dev/null)

if [ -z "$loose_files" ]; then
  log "根目錄沒有散檔，跳過"
  exit 0
fi

file_list=$(echo "$loose_files" | xargs -I{} basename {})
log "發現散檔：$(echo "$file_list" | tr '\n' ', ')"

prompt="這是 Google 雲端硬碟根目錄（$DRIVE），裡面已經有 6 個既有分類資料夾：
📱 媒體、📚 學習 & 筆記、📊 投資理財、📁 文件 & 保險、✈️ 旅遊、💻 程式 & 技術
以及一個 待整理/ 資料夾（給不確定分類的檔案用）、一個 小七卡/ 資料夾（不要動它）。

根目錄最外層目前有這些散檔誤放在那裡，還沒歸類：
$file_list

請針對每一個檔案：
1. 看檔名判斷內容類型，讀那 6 個分類資料夾底下現有的子資料夾名稱（用 ls 看，不用讀檔案內容）當作參考
2. 如果有明確對應的既有子資料夾，就把檔案搬進去（用 mv，不要新建分類資料夾，只能用既有的子資料夾，除非真的很明顯需要新建才建）
3. 如果不確定該歸哪一類，就搬進 待整理/ 資料夾，不要用猜的硬塞進某個分類

【安全規則】
- 只能動根目錄最外層列出的這幾個檔案，不要動 待整理/、小七卡/、6大分類資料夾裡面現有的任何檔案
- 不要刪除任何檔案，只能搬移
- 這是無人值守自動執行，不會有人跟你互動確認

完成後用繁體中文簡短總結：每個檔案搬去哪裡了（分類資料夾或待整理）。"

result=$(cd "$DRIVE" && timeout --kill-after=10 600 claude -p "$prompt" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Bash,Grep,Glob" \
  --disallowedTools "Agent,Workflow,Write,Edit" \
  --output-format text 2>>"$LOG")
exit_code=$?

log "結果 (exit=$exit_code)：$result"

if [ "$exit_code" -eq 0 ] && [ -n "$result" ]; then
  send_telegram "📁 雲端硬碟散檔歸位完成：
$result"
else
  send_telegram "⚠️ 雲端硬碟歸位執行失敗 (exit=$exit_code)，詳見 $LOG"
fi
