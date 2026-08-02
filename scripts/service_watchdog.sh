#!/bin/bash
# 服務看門狗 — 每 5 分鐘由 cron 執行
# 偵測到服務掛掉時自動重啟，並發 Telegram 通知

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

TELEGRAM_TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
TELEGRAM_CHAT_ID="7556217543"
LOG="/Users/steven/CCProject/logs/service_watchdog.log"
LOCK_DIR="/tmp/watchdog_locks"
mkdir -p "$LOCK_DIR"

send_telegram() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=$1" > /dev/null 2>&1
}

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
}

is_port_up() {
  nc -z localhost "$1" 2>/dev/null
}

check_and_restart() {
  local name="$1"
  local port="$2"
  local cmd="$3"
  local cwd="$4"
  local svc_log="$5"
  local lock_file="$LOCK_DIR/${name}.lock"

  if is_port_up "$port"; then
    # 若之前有 lock（重啟成功），清除它
    [ -f "$lock_file" ] && rm -f "$lock_file"
    return
  fi

  # 30 分鐘內已嘗試過 → 跳過，避免重啟風暴
  if [ -f "$lock_file" ]; then
    local age=$(( $(date +%s) - $(stat -f %m "$lock_file" 2>/dev/null || echo 0) ))
    if [ "$age" -lt 1800 ]; then
      return
    fi
  fi

  log "RESTART $name (port $port)"
  touch "$lock_file"

  (cd "$cwd" && eval "nohup $cmd >> \"$svc_log\" 2>&1 &")
  sleep 5

  if is_port_up "$port"; then
    log "OK $name 重啟成功"
    send_telegram "✅ [Watchdog] $name (port $port) 已自動重啟"
    rm -f "$lock_file"
  else
    log "FAIL $name 重啟失敗"
    send_telegram "❌ [Watchdog] $name (port $port) 重啟失敗，請手動檢查"
  fi
}

# LaunchAgent(KeepAlive) 管理的服務：用 kickstart 重啟，不用 nohup 以免雙進程
check_launchagent() {
  local name="$1"
  local port="$2"
  local label="$3"
  local lock_file="$LOCK_DIR/${name}.lock"

  if is_port_up "$port"; then
    [ -f "$lock_file" ] && rm -f "$lock_file"
    return
  fi
  if [ -f "$lock_file" ]; then
    local age=$(( $(date +%s) - $(stat -f %m "$lock_file" 2>/dev/null || echo 0) ))
    [ "$age" -lt 1800 ] && return
  fi
  log "KICKSTART $name (port $port)"
  touch "$lock_file"
  launchctl kickstart -k "gui/$(id -u)/$label"
  sleep 5
  if is_port_up "$port"; then
    log "OK $name 重啟成功"
    send_telegram "✅ [Watchdog] $name (port $port) 已透過 launchctl 重啟"
    rm -f "$lock_file"
  else
    log "FAIL $name 重啟失敗"
    send_telegram "❌ [Watchdog] $name (port $port) 重啟失敗，請手動檢查"
  fi
}

# ── 服務清單 ─────────────────────────────────────────────────────

check_launchagent "command-center" 5950 "com.steven.command-center"

check_and_restart "rabbit-care"       5200 \
  "venv/bin/python app.py" \
  "/Users/steven/CCProject/rabbit-care" \
  "/Users/steven/CCProject/logs/rabbit-care-web.log"

check_and_restart "news-analyzer"     5300 \
  "venv/bin/python app.py" \
  "/Users/steven/CCProject/news-analyzer" \
  "/Users/steven/CCProject/news-analyzer/app.log"

check_and_restart "daytrade-replay"   5400 \
  "venv/bin/python app.py" \
  "/Users/steven/CCProject/daytrade-replay" \
  "/Users/steven/CCProject/logs/daytrade-replay.log"

check_and_restart "stock-screener-ai" 5500 \
  "venv/bin/python app.py" \
  "/Users/steven/CCProject/stock-screener-ai" \
  "/Users/steven/CCProject/logs/stock-screener-ai.log"


check_and_restart "dashboard"         5600 \
  "/opt/homebrew/bin/python3.14 app.py" \
  "/Users/steven/CCProject/dashboard" \
  "/Users/steven/CCProject/dashboard/dashboard.log"

check_and_restart "dsa-webui"         5650 \
  "/opt/homebrew/bin/python3.14 main.py --webui-only --port 5650" \
  "/Users/steven/CCProject/daily-stock-analysis" \
  "/Users/steven/CCProject/logs/dsa-webui.log"

check_and_restart "dsa-backend"       8000 \
  "venv/bin/python main.py --serve" \
  "/Users/steven/CCProject/daily-stock-analysis" \
  "/Users/steven/CCProject/logs/dsa-backend.log"

check_and_restart "dsa-vite"          5173 \
  "/opt/homebrew/bin/npm run dev" \
  "/Users/steven/CCProject/daily-stock-analysis/apps/dsa-web" \
  "/Users/steven/CCProject/logs/dsa_vite.log"

check_and_restart "guru-tracker"      5910 \
  "venv/bin/python3 app.py" \
  "/Users/steven/CCProject/guru-tracker" \
  "/Users/steven/CCProject/logs/guru-tracker.log"


# ── 可選服務（目前非必要，掛了不自動重啟，只記 log）──────────────
for name_port in "stock_analyzer:5100" "portfolio-analyzer:5800"; do
  n="${name_port%%:*}"
  p="${name_port##*:}"
  if ! is_port_up "$p"; then
    log "DOWN $n (port $p) — 非必要服務，不自動重啟"
  fi
done
