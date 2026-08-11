#!/bin/bash
# Hermes 夜間訓練管線 — 每晚 01:00 由 LaunchAgent 觸發
# 把當天純問答任務重跑給 Hermes，比對差距、寫教材、同步進 Hermes system prompt
# 不推播 Telegram — 全部結果只寫進本機摘要檔，供 Steven 自行查看品質後再決定要不要開推播

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

PROJECT_DIR="$HOME/CCProject"
WORK_DIR="$PROJECT_DIR/hermes-training"
LOG="$PROJECT_DIR/logs/hermes_nightly_training.log"
DATE_STR="${1:-$(date '+%Y-%m-%d')}"
TRANSCRIPT_DIR="$HOME/.claude/projects/-Users-steven-CCProject"
TASKS_FILE="$WORK_DIR/tasks_${DATE_STR}.json"
SUMMARY_FILE="$WORK_DIR/logs/summary_${DATE_STR}.md"
DAILY_LOG_FILE="$WORK_DIR/logs/${DATE_STR}.md"
VAULT_DIR="$HOME/我的雲端硬碟/📚 學習 & 筆記/from Google keep/Projects/Hermes 夜間訓練日誌"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
write_summary() {
  mkdir -p "$WORK_DIR/logs"
  echo "$1" >> "$SUMMARY_FILE"
}
copy_logs_to_obsidian() {
  # Google Drive 桌面版沒在跑的話，vault 資料夾可能是舊快照；啟動它再複製，
  # 複製失敗也不能讓整條訓練管線失敗，只記警告
  if ! pgrep -f "Google Drive.app" > /dev/null 2>&1; then
    open -a "Google Drive" 2>>"$LOG"
    sleep 5
  fi
  if [ -d "$VAULT_DIR" ]; then
    [ -f "$DAILY_LOG_FILE" ] && cp "$DAILY_LOG_FILE" "$VAULT_DIR/${DATE_STR}.md" 2>>"$LOG"
    [ -f "$SUMMARY_FILE" ] && cp "$SUMMARY_FILE" "$VAULT_DIR/summary_${DATE_STR}.md" 2>>"$LOG"
    log "已複製當天 log 進 Obsidian vault"
  else
    log "警告：Obsidian vault 資料夾不存在，跳過複製（$VAULT_DIR）"
  fi
}

TRANSCRIPTS=()
while IFS= read -r f; do
  TRANSCRIPTS+=("$f")
done < <(find "$TRANSCRIPT_DIR" -maxdepth 1 -name "*.jsonl" -newermt "$DATE_STR 00:00:00" ! -newermt "$DATE_STR 23:59:59" 2>/dev/null)

if [ ${#TRANSCRIPTS[@]} -eq 0 ]; then
  log "今天沒有 session transcript，跳過"
  write_summary "🌙 Hermes 夜間訓練：今晚沒有可訓練的任務"
  exit 0
fi

python3 "$WORK_DIR/extract_tasks.py" "${TRANSCRIPTS[@]}" > "$TASKS_FILE" 2>>"$LOG"

python3 "$WORK_DIR/extract_scheduled_query_tasks.py" > "$WORK_DIR/scheduled_tasks_${DATE_STR}.json" 2>>"$LOG"
python3 -c "
import json
a = json.load(open('$TASKS_FILE'))
b = json.load(open('$WORK_DIR/scheduled_tasks_${DATE_STR}.json'))
json.dump(a + b, open('$TASKS_FILE', 'w'), ensure_ascii=False, indent=2)
" 2>>"$LOG"

TASK_COUNT=$(python3 -c "import json; print(len(json.load(open('$TASKS_FILE'))))" 2>>"$LOG")

if [ -z "$TASK_COUNT" ]; then
  log "任務擷取失敗，TASK_COUNT 無法解析"
  write_summary "⚠️ Hermes 夜間訓練：任務擷取失敗，詳見 log：$LOG"
  exit 0
fi

if [ "$TASK_COUNT" -eq 0 ]; then
  log "過濾後沒有可訓練任務"
  write_summary "🌙 Hermes 夜間訓練：今晚 0 條可訓練任務（可能都涉及寫入/修改操作）"
  exit 0
fi

log "今晚可訓練任務數：$TASK_COUNT"

PROMPT="$(sed "s|{{TASKS_FILE}}|$TASKS_FILE|g; s|{{DATE_STR}}|$DATE_STR|g" "$WORK_DIR/nightly_prompt_template.txt")"

result=$(cd "$PROJECT_DIR" && timeout --kill-after=30 7200 claude -p "$PROMPT" \
  --permission-mode default \
  --allowedTools "Read,Bash,Grep,Glob" \
  --disallowedTools "Agent,Workflow,Write,Edit" \
  --output-format text 2>>"$LOG")
exit_code=$?

log "結果 (exit=$exit_code)：$result"

if [ "$exit_code" -eq 0 ] && [ -n "$result" ]; then
  write_summary "🌙 Hermes 夜間訓練完成 ($DATE_STR)

${result}"
else
  write_summary "⚠️ Hermes 夜間訓練失敗（exit=$exit_code），詳見 log：$LOG"
fi

copy_logs_to_obsidian

# 清掉超過 7 天的中間檔案，避免無限累積
find "$WORK_DIR" -maxdepth 1 -name "scheduled_tasks_*.json" -mtime +7 -delete
find "$WORK_DIR" -maxdepth 1 -name "tasks_*.json" -mtime +7 -delete
