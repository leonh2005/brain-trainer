#!/bin/bash
# 用法：publish_report.sh <報告路徑> [发布路径]
#  Publishing：拷到 reports/ → commit → push → 驗證 → Telegram 通知
set -euo pipefail

REPO=/Users/steven/CCProject
SRC="${1:?需要報告路徑}"
DEST_REL="${2:-$(basename "$SRC")}"
DEST_REL="reports/${DEST_REL}"
SRC_ABS="$SRC"
[[ "$SRC_ABS" != /* ]] && SRC_ABS="$REPO/$SRC_ABS"

if [[ ! -f "$SRC_ABS" ]]; then
  echo "找不到檔案：$SRC_ABS" >&2
  exit 1
fi
if [[ "$SRC_ABS" != *.html ]]; then
  echo "只接受 .html 報告" >&2
  exit 1
fi
if grep -Eiq 'bot[0-9]{6,}:|sk-[A-Za-z0-9]{16,}|ocid1\.|-----BEGIN' "$SRC_ABS"; then
  echo "報告疑似含敏感字串，拒絕發布" >&2
  exit 1
fi

mkdir -p "$REPO/$(dirname "$DEST_REL")"
cp "$SRC_ABS" "$REPO/$DEST_REL"
cd "$REPO"
git add "$DEST_REL"
if ! git diff --cached --quiet; then
  git commit -m "docs: 發布報告 $(basename "$DEST_REL")"
fi
git push origin main

URL="https://leonh2005.github.io/brain-trainer/${DEST_REL}"
for i in 1 2 3 4 5 6 7 8; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$URL" || true)
  [[ "$code" == "200" ]] && break
  sleep 8
done
if [[ "${code:-}" != "200" ]]; then
  echo "Pages 驗證失敗（$code），但已推送：$URL" >&2
  exit 1
fi

TOKEN="$(cat "$HOME/CCProject/.secrets/telegram_token.txt")"
/usr/bin/python3 - "$URL" "$TOKEN" <<'PY'
import json, sys, urllib.parse, urllib.request
url, token = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=urllib.parse.urlencode({"chat_id": "7556217543", "text": f"📄 新報告：\n{url}"}).encode(),
)
json.load(urllib.request.urlopen(req, timeout=20))
PY
echo "已發布：$URL"
