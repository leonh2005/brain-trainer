#!/bin/bash
# 每晚 23:50 自動同步（取代少用的登出流程機械部分）
# 記憶更新靠對話即時做，此腳本只做 commit/push/rsync。
set -uo pipefail

mkdir -p /Users/steven/CCProject/logs
LOG=/Users/steven/CCProject/logs/nightly_sync.log
exec >> "$LOG" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly_sync 開始 ====="

STAMP=$(date '+%Y-%m-%d %H:%M')

# 1. youtube-monitor（只同步已追蹤檔案的變更）
if cd /Users/steven/youtube-monitor 2>/dev/null; then
  git add -u
  if ! git diff --cached --quiet; then
    git commit -m "chore: 每晚同步 $STAMP" && git push origin main && echo "youtube-monitor: 已推送"
  else
    echo "youtube-monitor: 無變更"
  fi
fi

# 2. CCProject：只同步「已追蹤檔案的變更」+ .claude(記憶/skills) + scripts
#    刻意不用 git add -A —— 避免把未追蹤的 TradingAgents/（內含 .env 金鑰）、
#    hermes-config/ 等敏感目錄誤推上 GitHub。
if cd /Users/steven/CCProject 2>/dev/null; then
  git add -u
  git add .claude/ scripts/ 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -m "chore: 每晚同步 $STAMP" && git push origin main && echo "CCProject: 已推送"
  else
    echo "CCProject: 無變更"
  fi
fi

# 3. rsync youtube-monitor 腳本到 VM telebot
rsync -avz -e "ssh -i /Users/steven/.ssh/oracle_line_bot -o ConnectTimeout=20 -o StrictHostKeyChecking=no" \
  /Users/steven/youtube-monitor/youtube_monitor.py \
  /Users/steven/youtube-monitor/douyin_monitor.py \
  /Users/steven/youtube-monitor/bd_monitor.py \
  ubuntu@161.33.6.190:~/telebot/ && echo "VM rsync: 完成" || echo "VM rsync: 失敗（VM 可能離線）"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') nightly_sync 結束 ====="
