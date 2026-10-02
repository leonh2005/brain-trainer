"""Telegram 入口：收訊息丟佇列、回覆 /confirm。只認 Steven 的 chat_id。"""
import json
import os
import time
import urllib.error
import urllib.request

import notify
import orchestrator
import taskqueue as tq

DEFAULT_DB = "queue.db"
CHAT_ID = notify.CHAT_ID
POLL_TIMEOUT = 30

HELP = (
    "指令：\n"
    "/plan <絕對路徑> :: <大任務>   （系統拆解，之後 /confirm）\n"
    "/add <絕對路徑> :: <任務>      （直接排隊）\n"
    "/ls                            列出任務\n"
    "/log <id>                      看單一任務\n"
    "/confirm <id>                  放行拆解出的子任務"
)


def _split(text):
    """把 '<路徑> :: <描述>' 拆成兩段；格式不對回 None。"""
    if "::" not in text:
        return None
    left, right = text.split("::", 1)
    cwd, spec = left.strip(), right.strip()
    if not cwd or not spec:
        return None
    return cwd, spec


def _require_cwd(cwd):
    cwd = os.path.abspath(os.path.expanduser(cwd))
    return cwd if os.path.isdir(cwd) else None


def _is_permanent(exc):
    """永久性錯誤（token 無效／被 webhook 佔用）——重試一萬次也不會好。"""
    return isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 409)


def handle(text, conn, chat_id):
    """處理一則訊息，回傳要回覆的字串（非授權來源回空字串）。"""
    if chat_id != CHAT_ID:
        return ""
    text = (text or "").strip()
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()

    if cmd in ("/help", "/start"):
        return HELP

    if cmd in ("/add", "/plan"):
        parts = _split(rest)
        if not parts:
            return "格式：命令 <絕對路徑> :: <描述>"
        cwd = _require_cwd(parts[0])
        if not cwd:
            return f"工作目錄不存在：{parts[0]}"
        kind = "orchestrator" if cmd == "/plan" else "task"
        tid = tq.add_task(conn, parts[1][:60], parts[1], cwd=cwd, kind=kind,
                          source="telegram")
        if kind == "orchestrator":
            return f"已建立拆解任務 {tid}；daemon 拆完會回報，再 /confirm {tid}"
        return f"已排入 {tid}"

    if cmd == "/ls":
        rows = tq.list_tasks(conn)
        if not rows:
            return "（沒有任務）"
        return "\n".join(f"{t['id']}  {t['status']:8s}  {t['title']}" for t in rows[-20:])

    if cmd == "/log":
        t = tq.get_task(conn, rest)
        if not t:
            return f"找不到 {rest}"
        out = [f"{t['id']}  {t['status']}  {t['title']}"]
        if t["result"]:
            out.append(t["result"][:1500])
        if t["error"]:
            out.append(f"錯誤：{t['error'][:500]}")
        return "\n".join(out)

    if cmd == "/confirm":
        try:
            n = orchestrator.confirm(conn, rest)
        except ValueError as exc:
            return str(exc)
        if n == 0:
            return "沒有可放行的子任務（可能還在拆解中，稍後再試）"
        return f"已放行 {n} 個子任務"

    return "不認得的指令，/help 看用法"


def _dispatch(upd, conn):
    """把一個 update 轉成回覆字串（不碰網路）。非訊息型別／非授權來源回 ""。"""
    msg = upd.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    return handle(msg.get("text", ""), conn, chat_id)


def get_updates(token, offset, timeout=POLL_TIMEOUT):
    url = (f"https://api.telegram.org/bot{token}/getUpdates"
           f"?timeout={timeout}&offset={offset}")
    with urllib.request.urlopen(url, timeout=timeout + 10) as resp:
        return json.loads(resp.read()).get("result", [])


def _drain(token, fetch=None, timeout=1):
    """丟棄積壓的 update，回傳下一個要用的 offset。

    不做這件事的話，bot 停機一天後重啟會把期間的每個指令重播一次（重複建任務、重複花錢）。"""
    fetch = fetch or get_updates
    offset = 0
    while True:
        ups = fetch(token, offset, timeout)
        if not ups:
            return offset
        offset = max(u["update_id"] for u in ups) + 1


def main_loop(db_path=DEFAULT_DB, poll=POLL_TIMEOUT):
    token = notify._token()
    if not token:
        print("[bot] 找不到 Telegram token，未啟動（見 README）", flush=True)
        return
    conn = tq.connect(db_path)
    tq.init_db(conn)
    offset = _drain(token)
    while True:
        try:
            for upd in get_updates(token, offset, poll):
                offset = upd["update_id"] + 1
                try:
                    reply = _dispatch(upd, conn)
                except Exception as exc:  # 單則失敗不可吃掉整個迴圈
                    reply = f"處理失敗：{exc}"[:200]
                if reply:
                    notify.send(reply, chat_id=CHAT_ID)
        except Exception as exc:
            if _is_permanent(exc):
                print(f"[bot] 永久性錯誤（{exc}）：token 無效或被 webhook 佔用，見 README",
                      flush=True)
                return
            print(f"[bot] 輪詢失敗：{exc!r}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main_loop()
