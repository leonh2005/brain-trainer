#!/bin/bash
# Oracle VM 連線監測 — 每小時 :30 執行
# SSH 連不上時推 Telegram，並嘗試用 OCI CLI 重開

TELEGRAM_TOKEN="8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
TELEGRAM_CHAT_ID="7556217543"
SSH_KEY="$HOME/.ssh/oracle_line_bot"
VM_HOST="ubuntu@161.33.6.190"
INSTANCE_ID="ocid1.instance.oc1.ap-osaka-1.anvwsljr6uhxtzycw2pdcmjakoqm3f7xqeuwiqee6kayoi72dmrxi34zz7xq"
LOCK="/tmp/vm_monitor.lock"
LOG="/Users/steven/CCProject/logs/vm_monitor.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

tg() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" -d "text=$1" > /dev/null
}

# SSH 連線測試（timeout 10 秒）
if ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
     -o BatchMode=yes "$VM_HOST" "echo ok" &>/dev/null; then
  log "OK"
  rm -f "$LOCK"
  exit 0
fi

log "DOWN — SSH 無回應"

# 30 分鐘內已觸發過，不重複通知
if [ -f "$LOCK" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  [ "$age" -lt 1800 ] && exit 0
fi
touch "$LOCK"

# 查 OCI 狀態
OCI_STATE=$(oci compute instance get --instance-id "$INSTANCE_ID" \
  --query "data.\"lifecycle-state\"" --raw-output 2>/dev/null)
log "OCI 狀態：$OCI_STATE"

if [ "$OCI_STATE" = "RUNNING" ]; then
  # VM 顯示 RUNNING 但 SSH 不通 → OS 掛了，SOFTRESET
  tg "⚠️ Oracle VM SSH 無回應（OCI 顯示 RUNNING）→ 自動 SOFTRESET 中..."
  oci compute instance action --action SOFTRESET --instance-id "$INSTANCE_ID" > /dev/null 2>&1
  log "已送出 SOFTRESET"
else
  tg "🔴 Oracle VM 離線（狀態：${OCI_STATE}）— 請手動確認"
  log "VM 非 RUNNING 狀態，未自動處理"
fi
