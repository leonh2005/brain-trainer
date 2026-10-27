#!/bin/bash
# log 輪替：檔案超過門檻就壓縮備份並清空原檔。
#
# 為什麼不用 macOS 的 newsyslog：它需要 root 權限才能跑，這個腳本不需要。
# 為什麼是「複製後清空」而不是「改名」：log 是 LaunchAgent 的 StandardOutPath，
# launchd 握著檔案的 fd。改名會讓服務繼續寫到改名後的檔案（空間沒省下），
# 複製+清空則保留同一個 inode，launchd 的 fd 照常寫入。
# 代價：複製與清空之間（毫秒級）新寫入的幾行會遺失——access log 可接受。
set -uo pipefail

LOG="${1:-/Users/steven/CCProject/logs/command-center.log}"
MAX_KB="${LOG_ROTATE_MAX_KB:-51200}"   # 門檻 50MB
KEEP="${LOG_ROTATE_KEEP:-5}"           # 保留 5 份壓縮檔
ROTATE_LOG="/Users/steven/CCProject/logs/log_rotate.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$ROTATE_LOG"; }

[ -f "$LOG" ] || exit 0

size_kb=$(( $(wc -c < "$LOG") / 1024 ))
if [ "$size_kb" -lt "$MAX_KB" ]; then
    exit 0
fi

# 1. 舊的往後移（.3.gz→.4.gz、.2.gz→.3.gz、.1.gz→.2.gz、.0.gz→.1.gz）
for i in 3 2 1 0; do
    [ -f "$LOG.$i.gz" ] && mv -f "$LOG.$i.gz" "$LOG.$((i+1)).gz"
done
# 2. 砍掉超出保留份數的
rm -f "$LOG.$((KEEP)).gz"

# 3. 目前的 log 壓縮成 .0.gz
if ! gzip -c "$LOG" > "$LOG.0.gz" 2>/dev/null; then
    log "✗ 壓縮失敗：${LOG}"
    exit 1
fi
# 4. 清空原檔（保留 inode 與權限，服務的 fd 不受影響）
: > "$LOG"

log "✓ 已輪替 ${LOG}（${size_kb}KB → .0.gz $(( $(wc -c < "$LOG.0.gz") / 1024 ))KB）"
