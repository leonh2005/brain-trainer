#!/bin/bash
# 用法：publish_report.sh <報告路徑> [reports/ 下的相對路徑]
set -euo pipefail

REPO=/Users/steven/CCProject
SRC="${1:?需要報告路徑}"
DEST_REL="${2:-$(basename "$SRC")}"

if [[ "$DEST_REL" != *.html || "$DEST_REL" == /* || "$DEST_REL" == *".."* ]]; then
  echo "目的路徑必須是 reports/ 下的 .html 相對路徑" >&2
  exit 1
fi
DEST_REL="reports/${DEST_REL}"
SRC_ABS="$SRC"
[[ "$SRC_ABS" != /* ]] && SRC_ABS="$REPO/$SRC_ABS"

if [[ ! -f "$SRC_ABS" ]]; then
  echo "找不到檔案：$SRC_ABS" >&2
  exit 1
fi
[[ "$SRC_ABS" == *.html ]] || { echo "只接受 .html 報告" >&2; exit 1; }

BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
[[ "$BRANCH" == "main" ]] || { echo "必須在 main 分支发布，目前 $BRANCH" >&2; exit 1; }

OTHER_STAGED=$(git -C "$REPO" diff --cached --name-only | grep -v "^${DEST_REL}$" || true)
[[ -z "$OTHER_STAGED" ]] || { echo "有其他已 staged 檔案，拒絕發布：$OTHER_STAGED" >&2; exit 1; }

# 先複製到暫存檔再掃描，避免 scan 與 publish 不是同一份內容
TMP=$(mktemp)
cp "$SRC_ABS" "$TMP"
if grep -Eiq 'bot[0-9]{6,}:|sk-[A-Za-z0-9_-]{16,}|sk-proj-|sk-ant-|ghp_|github_pat_|gho_|ghu_|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-|ocid1\.|-----BEGIN [A-Z ]*PRIVATE KEY' "$TMP"; then
  rm -f "$TMP"
  echo "報告疑似含敏感字串，拒絕發布" >&2
  exit 1
fi

mkdir -p "$REPO/$(dirname "$DEST_REL")"
if [[ "$SRC_ABS" != "$REPO/$DEST_REL" ]]; then
  cp "$TMP" "$REPO/$DEST_REL"
else
  cp "$TMP" "$SRC_ABS"
fi
rm -f "$TMP"

cd "$REPO"
git add "$DEST_REL"
if ! git diff --cached --quiet -- "$DEST_REL"; then
  git commit -m "docs: 發布報告 $(basename "$DEST_REL")"
fi
git push origin main

URL="https://leonh2005.github.io/brain-trainer/${DEST_REL}"
LOCAL_HASH=$(shasum -a 256 "$REPO/$DEST_REL" | awk '{print $1}')
for i in $(seq 1 12); do
  REMOTE_HASH=$(curl -s -m 15 "$URL" | shasum -a 256 | awk '{print $1}')
  [[ "$REMOTE_HASH" == "$LOCAL_HASH" ]] && break
  sleep 8
done
if [[ "${REMOTE_HASH:-}" != "$LOCAL_HASH" ]]; then
  echo "Pages 內容驗證失敗，但已推送：$URL" >&2
  exit 1
fi

# token 在 python 內自己讀檔，不出現在 process list
/usr/bin/python3 - "$URL" <<'PY'
import json, os, sys, urllib.parse, urllib.request
url = sys.argv[1]
token = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt"), encoding="utf-8").read().strip()
req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=urllib.parse.urlencode({"chat_id": "7556217543", "text": f"📄 新報告：\n{url}"}).encode(),
)
json.load(urllib.request.urlopen(req, timeout=20))
PY
echo "已發布：$URL"
