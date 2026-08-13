#!/bin/bash
# Hermes 補訓管線 — 把過去幾天還沒訓練過的 session transcript 往回拉，逐天餵給 run_nightly.sh 訓練
# 只在早上 07:00 前執行，超過就停止（避免影響 Steven 白天用電腦）
# 用法：bash run_backfill.sh

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

PROJECT_DIR="$HOME/CCProject"
WORK_DIR="$PROJECT_DIR/hermes-training"
TRANSCRIPT_DIR="$HOME/.claude/projects/-Users-steven-CCProject"
LOG="$PROJECT_DIR/logs/hermes_backfill.log"
CUTOFF_HOUR="${HERMES_BACKFILL_CUTOFF_HOUR:-7}"
TODAY="$(date '+%Y-%m-%d')"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

past_cutoff() {
  local hour
  hour="$(date '+%H')"
  [ "$((10#$hour))" -ge "$CUTOFF_HOUR" ]
}

# 找出所有有 transcript 的日期，去掉今天（今天還沒過完，交給 01:00 的正常排程處理）
DATES=()
while IFS= read -r d; do
  DATES+=("$d")
done < <(find "$TRANSCRIPT_DIR" -maxdepth 1 -name "*.jsonl" -exec stat -f "%Sm" -t "%Y-%m-%d" {} \; 2>/dev/null | sort -u -r)

log "候選日期（含今天）：${DATES[*]}"

for d in "${DATES[@]}"; do
  if past_cutoff; then
    log "已到設定的 ${CUTOFF_HOUR}:00 截止時間，停止補訓"
    break
  fi

  if [ "$d" = "$TODAY" ]; then
    continue
  fi

  if [ -f "$WORK_DIR/logs/${d}.md" ]; then
    log "$d 已經訓練過，跳過"
    continue
  fi

  log "開始補訓 $d"
  bash "$WORK_DIR/run_nightly.sh" "$d"
  log "$d 補訓完成"
done

log "補訓迴圈結束"
