#!/usr/bin/env python3
"""Oracle 搶機每日 22:00 摘要 → Telegram

log 只有 [HH:MM:SS] 沒有日期，而且搶機腳本重啟後輪數會從 1 重算，
所以「上次報告後跑了幾輪」用 log 檔案位移量（offset）計算，不用輪數相減。
"""
import collections
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Tuple

LOG_PATH = os.path.expanduser("~/CCProject/logs/oracle_a1_grab.log")
RESULT_PATH = os.path.expanduser("~/CCProject/logs/oracle_a1_result.txt")
STATE_PATH = os.path.expanduser("~/.claude/oracle_report_state.json")
TOKEN_PATH = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
TG_CHAT = "7556217543"

ROUND_RE = re.compile(r"第 (\d+) 輪：已有 (\d+)/(\d+)")
REASON_RE = re.compile(r"(line-bot-a1-\d+)：(.+)$")


def send(text: str) -> None:
    if not os.path.exists(TOKEN_PATH):
        print("找不到 telegram token，略過")
        return
    with open(TOKEN_PATH, encoding="utf-8") as f:
        token = f.read().strip()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode(),
        timeout=15,
    )


def parse(handle) -> Tuple[int, int, int, Optional[str], collections.Counter]:
    """回傳 (輪數, 已有台數, 目標台數, 最後原因, 原因統計)"""
    rounds, have, target = 0, 0, 3
    last_reason = None
    counts: collections.Counter = collections.Counter()
    for line in handle:
        m = ROUND_RE.search(line)
        if m:
            rounds += 1
            have, target = int(m.group(2)), int(m.group(3))
            continue
        r = REASON_RE.search(line)
        if r:
            counts[r.group(2).strip()] += 1
            last_reason = f"{r.group(1)} {r.group(2).strip()}"
    return rounds, have, target, last_reason, counts


def main() -> None:
    if not os.path.exists(LOG_PATH):
        send("⛏️ Oracle 搶機日報\n\n找不到 log，搶機程序可能從未啟動")
        return

    size = os.path.getsize(LOG_PATH)
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            offset = json.load(f).get("offset", 0)
    except (OSError, ValueError):
        offset = 0
    if offset > size:  # log 被清空或重建
        offset = 0

    # 現況：一律看最後 400 行
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        tail = collections.deque(f, maxlen=400)
    _, have, target, last_reason, _ = parse(tail)
    cur_round = 0
    for line in tail:
        m = ROUND_RE.search(line)
        if m:
            cur_round = int(m.group(1))

    # 上次報告後新增的量
    if offset:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            new_rounds, _, _, _, counts = parse(f)
    else:
        new_rounds, counts = 0, collections.Counter()

    running = subprocess.run(
        ["pgrep", "-f", "oracle_setup.py"], capture_output=True
    ).returncode == 0

    if os.path.exists(RESULT_PATH):
        status = "✅ 已完成（結果檔存在）"
    elif running:
        status = "🟢 執行中"
    else:
        status = "🔴 未在執行（LaunchAgent 可能被卸載或崩潰）"

    lines = [
        f"⛏️ Oracle 搶機日報（{datetime.now().strftime('%m/%d %H:%M')}）",
        "",
        f"狀態：{status}",
        f"進度：{have} / {target} 台",
        f"目前輪數：第 {cur_round} 輪",
    ]
    if offset == 0:
        lines.append("上次報告後：首份報告，無從比較")
    else:
        detail = ""
        if counts:
            detail = "（" + "、".join(f"{k} {v}" for k, v in counts.most_common()) + "）"
        lines.append(f"上次報告後：+{new_rounds} 輪{detail}")
    lines += [
        f"最後一輪：{last_reason or '（尚無）'}",
        "",
        "目標：3 台 VM.Standard.A1.Flex（1 OCPU / 6 GB / 50 GB，大阪）",
    ]

    send("\n".join(lines))

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "offset": size}, f)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
