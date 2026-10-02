#!/bin/bash
# 換掉 agent-orchestrator 的 Telegram bot token。
# 互動式：token 不會顯示、不進 shell history、不經過 Claude 對話。
set -euo pipefail

TARGET="$HOME/CCProject/.secrets/orchestrator_token.txt"
LABEL="gui/$(id -u)/com.steven.agent-orchestrator-bot"

printf '請貼上新的 bot token（輸入不會顯示，貼上後按 Enter）：'
read -rs NEW
echo
[ -n "${NEW:-}" ] || { echo "沒有輸入，取消。"; exit 1; }

umask 077
printf '%s' "$NEW" > "$TARGET"
chmod 600 "$TARGET"
echo "✅ 已寫入 ${TARGET}（0600）"

python3 - "$TARGET" <<'PY'
import json, sys, urllib.request
token = open(sys.argv[1]).read().strip()
try:
    me = json.loads(urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/getMe", timeout=10).read())
    print("✅ token 有效，bot 是：", me["result"]["username"])
except Exception as exc:
    print("❌ token 驗證失敗：", exc)
    sys.exit(1)
PY

if launchctl kickstart -k "$LABEL" 2>/dev/null; then
    echo "✅ 已重啟 bot"
else
    echo "⚠️  bot 重啟失敗，檢查 $LABEL"
fi

echo
echo "提醒：換了 bot 之後，要先到新 bot 那邊傳一句話（例如 /start），"
echo "      否則 daemon 的推播會回 400 chat not found。"
