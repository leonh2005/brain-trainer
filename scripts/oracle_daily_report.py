#!/usr/bin/env python3
"""Oracle 搶機每日 22:00 摘要 → Telegram

log 只有 [HH:MM:SS] 沒有日期，而且搶機腳本重啟後輪數會從 1 重算，
所以「上次報告後跑了幾輪」用 log 檔案位移量（offset）計算，不用輪數相減。
搶機程序是否存活用它的 flock 判斷（oracle_setup.py 執行期間持有該鎖）。
"""
import collections
import fcntl
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Counter, Optional, Tuple

LOG_PATH = os.path.expanduser("~/CCProject/logs/oracle_a1_grab.log")
LOCK_PATH = os.path.expanduser("~/CCProject/logs/.oracle_a1_grab.lock")
RESULT_PATH = os.path.expanduser("~/CCProject/logs/oracle_a1_result.txt")
STATE_PATH = os.path.expanduser("~/.claude/oracle_report_state.json")
TOKEN_PATH = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
TG_CHAT = "7556217543"

ROUND_RE = re.compile(r"第 (\d+) 輪：已有 (\d+)/(\d+)")
TARGET_RE = re.compile(r"目標：(.+)$")
# 實例名不寫死前綴：任何「<名稱>：容量不足/429/網路錯誤/HTTP xxx」都算
REASON_RE = re.compile(
    r"^\s*\[[\d:]+\]\s+(\S+)：(容量不足|請求頻率過高|網路錯誤.*|HTTP \d+.*)$"
)

ParseResult = Tuple[int, int, int, int, Optional[str], Counter]


def parse(handle) -> ParseResult:
    """回傳 (輪數, 已有台數, 目標台數, 最後輪次, 最後原因, 原因統計)"""
    rounds, have, target, last_round = 0, 0, 3, 0
    last_reason = None
    counts: Counter = collections.Counter()
    for line in handle:
        m = ROUND_RE.search(line)
        if m:
            rounds += 1
            last_round = int(m.group(1))
            have, target = int(m.group(2)), int(m.group(3))
            continue
        r = REASON_RE.search(line)
        if r:
            counts[r.group(2).strip()] += 1
            last_reason = f"{r.group(1)} {r.group(2).strip()}"
    return rounds, have, target, last_round, last_reason, counts


def last_target_line(handle) -> Optional[str]:
    """搶機腳本啟動時會印「目標：...」，直接沿用，避免日報寫死的描述過期"""
    found = None
    for line in handle:
        m = TARGET_RE.search(line)
        if m:
            found = m.group(1).strip()
    return found


def is_running() -> bool:
    """搶機程序執行期間對 lock 檔持有 flock；能拿到鎖代表沒在跑"""
    if not os.path.exists(LOCK_PATH):
        return False
    try:
        with open(LOCK_PATH, "r") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def load_offset() -> Optional[int]:
    """回傳上次報告時的檔案位移；None = 從未報告過"""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return None
        return int(state.get("offset", 0))
    except (OSError, ValueError, TypeError):
        return None


def save_offset(offset: int) -> None:
    """原子寫入，避免中途被砍留下半截 JSON"""
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)
    os.replace(tmp, STATE_PATH)


def delta_start(prev: Optional[int], size: int) -> Optional[int]:
    """從哪個位移開始算「新增」。None = 首份報告；檔案變短（截斷/重建）就從頭算"""
    if prev is None:
        return None
    return 0 if prev > size else prev


def send(text: str) -> bool:
    if not os.path.exists(TOKEN_PATH):
        print("找不到 telegram token，日報未送出", file=sys.stderr)
        return False
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            token = f.read().strip()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode(),
            timeout=15,
        )
        return True
    except Exception as e:  # noqa: BLE001 - 任何失敗都要回報，且不可印出含 token 的 URL
        print(f"Telegram 發送失敗：{type(e).__name__}", file=sys.stderr)
        return False


def main() -> int:
    if not os.path.exists(LOG_PATH):
        send("⛏️ Oracle 搶機日報\n\n找不到 log，搶機程序可能從未啟動")
        return 1

    size = os.path.getsize(LOG_PATH)

    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        tail = collections.deque(f, maxlen=400)
    _, have, target, cur_round, last_reason, _ = parse(tail)

    # 「目標：…」只在每次啟動時印一次，通常在 tail 之外，要掃全檔才找得到
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        target_line = last_target_line(f)

    start = delta_start(load_offset(), size)
    if start is None:
        new_rounds, counts = 0, collections.Counter()
    elif size == 0:
        new_rounds, counts = 0, collections.Counter()
    else:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            new_rounds, _, _, _, _, counts = parse(f)

    if os.path.exists(RESULT_PATH):
        status = "✅ 已完成（結果檔存在）"
    elif is_running():
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
    if start is None:
        lines.append("上次報告後：首份報告，無從比較")
    else:
        detail = ""
        if counts:
            detail = "（" + "、".join(f"{k} {v}" for k, v in counts.most_common()) + "）"
        lines.append(f"上次報告後：+{new_rounds} 輪{detail}")
    lines.append(f"最後一輪：{last_reason or '（尚無）'}")
    if target_line:
        lines += ["", f"目標：{target_line}"]

    if not send("\n".join(lines)):
        return 1  # 沒送出就不記 offset，下次才不會誤判「已報告」

    save_offset(size)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
