#!/bin/bash
# Mac garbage cleanup: Playwright Firefox profiles, Homebrew old versions, stale App Caches

LOG="/tmp/mac-cleanup.log"
exec > "$LOG" 2>&1

echo "=== Mac Cleanup $(date '+%Y-%m-%d %H:%M:%S') ==="

# 1. Playwright leftover Firefox profiles
COUNT=$(find /private/tmp -maxdepth 1 -mindepth 1 -type d -name "tmp[0-9a-z_]*" 2>/dev/null | wc -l | xargs)
if [ "$COUNT" -gt 0 ]; then
    BEFORE=$(du -sk /private/tmp 2>/dev/null | awk '{print $1}')
    find /private/tmp -maxdepth 1 -mindepth 1 -type d -name "tmp[0-9a-z_]*" -exec rm -rf {} + 2>/dev/null
    AFTER=$(du -sk /private/tmp 2>/dev/null | awk '{print $1}')
    FREED=$(( (BEFORE - AFTER) / 1024 ))
    echo "Playwright tmp: removed $COUNT dirs, freed ${FREED}MB"
else
    echo "Playwright tmp: nothing to clean"
fi

# 2. Homebrew old versions
BREW_OUT=$(brew cleanup -s 2>&1 | grep -o 'freed approximately.*')
echo "Homebrew: ${BREW_OUT:-nothing to clean}"

# 3. pip cache
rm -rf ~/Library/Caches/pip 2>/dev/null
echo "pip cache: cleared"

# 4. App Caches unused for 30+ days
OLD=$(find ~/Library/Caches -maxdepth 1 -type d -atime +30 2>/dev/null | wc -l | xargs)
find ~/Library/Caches -maxdepth 1 -type d -atime +30 -exec rm -rf {} + 2>/dev/null
echo "Stale App Caches (30d+): removed $OLD dirs"

# 5. Disk status
AVAIL=$(df -h / | awk 'NR==2{print $4}')
USED=$(df -h / | awk 'NR==2{print $5}')
echo "Disk: ${AVAIL} free (${USED} used)"

# 6. Telegram notification
MSG=$(cat "$LOG")
curl -s -X POST "https://api.telegram.org/bot$(cat "$HOME/CCProject/.secrets/telegram_token.txt")/sendMessage" \
    -d "chat_id=7556217543" \
    --data-urlencode "text=🧹 Mac Cleanup
${MSG}"
