#!/bin/zsh
set -euo pipefail
VM="ubuntu@161.33.42.181"
KEY="$HOME/.ssh/oracle_line_bot"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$VM")
START_TS="${1:-$(date +%s)}"
TOKEN_FILE="$HOME/CCProject/.secrets/telegram_token.txt"
STATUS_FILE="$HOME/CCProject/logs/oracle_pipeline_done.status"
mkdir -p "$HOME/CCProject/logs"

while true; do
  if "${SSH[@]}" 'test -f /mnt/data/final_animation_mod.mp4 && grep -q "FINAL /mnt/data/final_animation_mod.mp4" /mnt/data/pipeline_to_done.log 2>/dev/null' >/dev/null 2>&1; then
    END_TS=$(date +%s)
    DUR=$((END_TS-START_TS))
    H=$((DUR/3600)); M=$(((DUR%3600)/60)); S=$((DUR%60))
    MSG="ComfyUI 動畫試作完成\n成品: /mnt/data/final_animation_mod.mp4 (VM 161.33.42.181)\n耗時: ${H}h ${M}m ${S}s\n(shot1-4 改樣貌 SVD 動畫已生成並合併)"
    echo "$MSG" > "$STATUS_FILE"
    if [ -f "$TOKEN_FILE" ]; then
      TOKEN=$(tr -d '\n' < "$TOKEN_FILE")
      curl -sS "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=7556217543" \
        --data-urlencode "text=$MSG" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  # if pipeline died with error, notify once
  if "${SSH[@]}" 'grep -q "ERROR missing" /mnt/data/pipeline_to_done.log 2>/dev/null' >/dev/null 2>&1; then
    END_TS=$(date +%s)
    DUR=$((END_TS-START_TS))
    H=$((DUR/3600)); M=$(((DUR%3600)/60)); S=$((DUR%60))
    MSG="ComfyUI 動畫試作流程出現錯誤，請檢查 /mnt/data/pipeline_to_done.log (VM 161.33.42.181)\n已耗時: ${H}h ${M}m ${S}s"
    echo "$MSG" > "$STATUS_FILE"
    if [ -f "$TOKEN_FILE" ]; then
      TOKEN=$(tr -d '\n' < "$TOKEN_FILE")
      curl -sS "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=7556217543" \
        --data-urlencode "text=$MSG" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  sleep 60
done
