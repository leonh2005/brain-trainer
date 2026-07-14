#!/bin/bash
# Mac 每週自動清理
LOG="/Users/steven/CCProject/logs/mac_cleanup.log"
BOT="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
CHAT="7556217543"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
freed=0

log "=== 開始清理 ==="

# pip cache
before=$(du -sm ~/.cache/pip ~/Library/Caches/pip 2>/dev/null | awk '{s+=$1}END{print s}')
pip3 cache purge >> "$LOG" 2>&1
after=$(du -sm ~/.cache/pip ~/Library/Caches/pip 2>/dev/null | awk '{s+=$1}END{print s}')
freed=$((freed + before - after))
log "pip cache: -$((before - after))MB"

# Homebrew
brew_out=$(brew cleanup --prune=all 2>&1)
brew_mb=$(echo "$brew_out" | grep -o '[0-9.]*MB' | head -1 | tr -d 'MB')
[ -n "$brew_mb" ] && freed=$((freed + ${brew_mb%.*}))
log "Homebrew: $brew_out"

# playwright / tmp 殘檔
find /private/tmp -maxdepth 1 \( \
  -name "playwright_chromiumdev_profile-*" \
  -o -name "playwright-artifacts-*" \
  -o -name "tmp_*" \
  -o -name "omi_swipe*.log" \
\) -exec rm -rf {} + 2>/dev/null
log "playwright/tmp_ 殘檔已清"

# 過期 watchdog lock（超過 2 小時）
find /tmp/watchdog_locks -name "*.lock" -mmin +120 -delete 2>/dev/null
log "watchdog locks 已清"

# macOS 系統 log（只清自己的）
rm -f ~/Library/Logs/*.log ~/Library/Logs/*/*.log 2>/dev/null
log "user logs 已清"

log "=== 完成，共釋出約 ${freed}MB ==="

# Telegram 通知
curl -s -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
  -d "chat_id=${CHAT}" \
  -d "text=🧹 Mac 週清完成
釋出約 ${freed}MB
$(date '+%Y-%m-%d %H:%M')" > /dev/null 2>&1
