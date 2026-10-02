# 常駐 Agent 編排系統 — Phase 4 實作計畫（Telegram 入口）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 從手機丟任務、看結果 —— Telegram 收訊息丟佇列、任務結束推播、回覆 `/confirm` 放行拆解。

**Architecture:** 一支常駐的 long-polling bot（stdlib `urllib`，無額外依賴）讀 Telegram 的 `getUpdates`，把訊息轉成佇列操作；daemon 在任務結束時推播。

**Tech Stack:** Python 3（stdlib：`urllib`／`json`）、pytest。沿用既有的 `taskqueue`／`orchestrator`。

**Spec:** `docs/superpowers/specs/2026-10-01-agent-orchestrator-design.md`（§8 入口）

## Global Constraints

- **信任模型（已定）**：只接受 `CHAT_ID = "7556217543"`（Steven）的訊息；其他來源一律忽略、不回。**能下指令 = 能在這台 Mac 上執行任意指令**，所以這是硬性白名單。
- **token 不寫進程式碼**：一律讀 `~/CCProject/.secrets/telegram_token.txt`（比照既有慣例）。讀不到就靜默停用（不炸）。
- 只用 stdlib（`urllib`／`json`／`time`），不引入 `python-telegram-bot` 等套件。
- **指令格式**：因為任務需要 `cwd`，`/plan` 與 `/add` 用 `<絕對路徑> :: <描述>` 形式。
- 推播失敗不可影響任務本身（`notify.send` 一律吞例外、回 `False`）。

## Review Focus

1. **非 Steven 的訊息被執行** → 必須完全忽略（Task 2）
2. **token 檔不存在／格式錯** → bot 要停用而不是崩潰，任務推送也不能因此壞掉（Task 1、3）
3. **`::` 分隔格式錯**（缺 `::`、cwd 不存在）→ 要回覆清楚的用法提示，不能建出指向錯目錄的任務（Task 2）
4. **推播擋住 worker** → 通知慢／失敗不能拖住任務收尾（Task 3）
5. **long polling 的中斷** → 網路錯誤要重試而不是讓 bot 死掉（Task 2）

---

### Task 1: 推播模組（notify.py）

**Files:**
- Create: `agent-orchestrator/notify.py`
- Test: `agent-orchestrator/tests/test_notify.py`

**Interfaces:**
- Produces: `notify.send(text: str, chat_id: str = CHAT_ID, timeout: int = 10) -> bool`；`notify.TOKEN_FILE`；`notify.CHAT_ID`

- [ ] **Step 1: 寫失敗的測試**

```python
import notify


def test_send_returns_false_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "TOKEN_FILE", tmp_path / "nope.txt")
    assert notify.send("hi") is False


def test_send_returns_false_on_http_error(tmp_path, monkeypatch):
    token = tmp_path / "tok.txt"
    token.write_text("dummy")
    monkeypatch.setattr(notify, "TOKEN_FILE", token)

    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("hi") is False   # 不可讓例外穿出
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_notify.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'notify'`

- [ ] **Step 3: 實作**

```python
"""Telegram 推播（只推給 Steven）。token 讀自 .secrets，讀不到就靜默停用。"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / "CCProject" / ".secrets" / "telegram_token.txt"
CHAT_ID = "7556217543"


def _token():
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return None


def send(text, chat_id=CHAT_ID, timeout=10):
    """推一則訊息；沒有 token 或失敗一律回 False，絕不讓例外影響呼叫端。"""
    token = _token()
    if not token:
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            return bool(json.loads(resp.read()).get("ok"))
    except Exception:
        return False
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_notify.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add notify.py tests/test_notify.py
git commit -m "feat: Telegram 推播模組"
```

---

### Task 2: Telegram bot（收訊息、放行確認）

**Files:**
- Create: `agent-orchestrator/telegram_bot.py`
- Test: `agent-orchestrator/tests/test_telegram_bot.py`

**Interfaces:**
- Produces:
  - `telegram_bot._split(text) -> tuple[str, str] | None`（拆 `<路徑> :: <描述>`；格式不對回 `None`）
  - `telegram_bot.handle(text, conn, chat_id) -> str`（回要回覆的字串；非授權 chat_id 回 `""`）
  - `telegram_bot.main_loop(db_path="queue.db", poll=30)`

- [ ] **Step 1: 寫失敗的測試**

```python
import telegram_bot as tb
import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def test_unauthorized_chat_is_ignored(tmp_path):
    conn = _conn(tmp_path)
    assert tb.handle("/ls", conn, "999") == ""


def test_help_lists_commands(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/help", conn, tb.CHAT_ID)
    assert "/plan" in out and "/add" in out


def test_add_requires_double_colon(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/add /tmp 沒有分隔", conn, tb.CHAT_ID)
    assert "::" in out          # 回覆要教他正確格式
    assert tq.list_tasks(conn) == []


def test_add_creates_task_with_cwd(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    out = tb.handle(f"/add {work} :: 做點事", conn, tb.CHAT_ID)
    tasks = tq.list_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0]["cwd"] == str(work)
    assert tasks[0]["spec"] == "做點事"
    assert tasks[0]["id"] in out


def test_plan_creates_orchestrator_task(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    tb.handle(f"/plan {work} :: 研究並報告", conn, tb.CHAT_ID)
    tasks = tq.list_tasks(conn)
    assert tasks[0]["kind"] == "orchestrator"


def test_add_rejects_missing_cwd(tmp_path):
    conn = _conn(tmp_path)
    out = tb.handle("/add /no/such/dir :: x", conn, tb.CHAT_ID)
    assert "目錄" in out
    assert tq.list_tasks(conn) == []


def test_ls_lists_tasks(tmp_path):
    conn = _conn(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    tb.handle(f"/add {work} :: 甲", conn, tb.CHAT_ID)
    assert "甲" in tb.handle("/ls", conn, tb.CHAT_ID)


def test_confirm_releases_children(tmp_path):
    conn = _conn(tmp_path)
    pid = tq.add_task(conn, "母", "x", kind="orchestrator")
    c1 = tq.add_task(conn, "子", "s")
    tq.set_status(conn, c1, "blocked", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    out = tb.handle(f"/confirm {pid}", conn, tb.CHAT_ID)
    assert "1" in out
    assert tq.get_task(conn, c1)["status"] == "pending"


def test_unknown_command_returns_hint(tmp_path):
    conn = _conn(tmp_path)
    assert "指令" in tb.handle("/bogus", conn, tb.CHAT_ID)
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_telegram_bot.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'telegram_bot'`

- [ ] **Step 3: 實作**

```python
"""Telegram 入口：收訊息丟佇列、回覆 /confirm。只認 Steven 的 chat_id。"""
import json
import os
import time
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
    if not os.path.isdir(cwd):
        return None
    return cwd


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
        tid = tq.add_task(conn, parts[1][:60], parts[1], cwd=cwd, kind=kind)
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
        return f"已放行 {n} 個子任務"

    return "不認得的指令，/help 看用法"


def get_updates(token, offset, timeout=POLL_TIMEOUT):
    url = (f"https://api.telegram.org/bot{token}/getUpdates"
           f"?timeout={timeout}&offset={offset}")
    with urllib.request.urlopen(url, timeout=timeout + 10) as resp:
        return json.loads(resp.read()).get("result", [])


def main_loop(db_path=DEFAULT_DB, poll=POLL_TIMEOUT):
    token = notify._token()
    if not token:
        raise SystemExit("找不到 Telegram token，bot 未啟動")
    conn = tq.connect(db_path)
    tq.init_db(conn)
    offset = 0
    while True:
        try:
            for upd in get_updates(token, offset, poll):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                reply = handle(msg.get("text", ""), conn, chat_id)
                if reply:
                    notify.send(reply, chat_id=chat_id)
        except Exception as exc:  # 網路錯誤不該讓 bot 死掉
            print(f"[bot] 輪詢失敗：{exc!r}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main_loop()
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_telegram_bot.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: Telegram 入口（收訊息、放行確認）"
```

---

### Task 3: daemon 任務結束推播

**Files:**
- Modify: `agent-orchestrator/daemon.py`
- Test: `agent-orchestrator/tests/test_daemon.py`（追加）

**Interfaces:**
- Produces: `daemon._notify_result(conn, task_id)`；`_run_worker` 在 `finally` 內呼叫它推播

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_run_worker_notifies_on_completion(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")
    sent = []
    monkeypatch.setattr(daemon.notify, "send", lambda text, **kw: sent.append(text) or True)
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)
    assert any(tid in s for s in sent)


def test_notify_failure_does_not_break_task(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")
    monkeypatch.setattr(daemon.notify, "send",
                        lambda text, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)     # 不該把例外穿出去
    assert tq.get_task(conn, tid)["status"] == "done"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_daemon.py -v -k notify`
Expected: FAIL（沒有推播）

- [ ] **Step 3: 實作**

頂部加 `import notify`；`_run_worker` 的 `finally` 之前補推播（**放在 `finally` 內、`conn.close()` 之前**，且整段包 try/except）：

```python
def _notify_result(conn, task_id):
    """任務結束時推播；任何失敗都不影響任務本身。"""
    try:
        task = tq.get_task(conn, task_id)
        if not task:
            return
        icon = {"done": "✅", "failed": "❌"}.get(task["status"])
        if not icon:
            return
        line = f"{icon} {task['title']}（{task['id']}）"
        if task["error"]:
            line += f"\n{task['error'][:500]}"
        notify.send(line)
    except Exception:
        pass
```

在 `_run_worker` 的 `finally:` 區塊內、`conn.close()` 之前呼叫：

```python
    finally:
        _notify_result(conn, task_id)
        conn.close()
```

> 放在 `finally` 是刻意的：例外路徑（worker 崩潰）也要推播。

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add daemon.py tests/test_daemon.py
git commit -m "feat: daemon 任務結束推播 Telegram"
```

---

### Task 4: 真實驗證與文件

**Files:**
- Modify: `agent-orchestrator/README.md`

- [ ] **Step 1: 用真 Telegram 驗證一輪**

```bash
cd ~/CCProject/agent-orchestrator
python3 telegram_bot.py     # 前景跑，然後在手機上丟：
#   /help
#   /add /tmp/orch_tg :: 建一個 tg.txt 內容放 TG
#   /ls
```

Expected：手機收到 `/help` 的指令清單；`/add` 回一個 task id；daemon 跑完後收到 ✅ 推播。

- [ ] **Step 2: 更新 `README.md`**

在「用法」段後加：

```markdown
## Telegram

```bash
python3 telegram_bot.py     # 常駐（long polling）
```

只用 Steven 的 chat_id。指令：`/help` 看全部。

任務需要工作目錄，所以 `/plan` 與 `/add` 用 `<絕對路徑> :: <描述>`：

```
/plan ~/CCProject/某專案 :: 研究 X 並整理成報告
/add  /tmp/work        :: 建一個 hello.txt
/confirm <母任務 id>
```
```

- [ ] **Step 3: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add README.md
git commit -m "docs: Telegram 入口用法（Phase 4 完成）"
```

---

## Phase 4 完成定義

- `pytest` 全綠。
- 手機上的 `/plan`／`/add`／`/ls`／`/confirm` 都能用；任務結束收到推播。
- 非 Steven 的 chat_id 完全被忽略。

## 後續（不在本計畫）

- **command-center 看板**（Flask 一頁：排隊／進行中／完成）
- daemon 與 bot 的 LaunchAgent 化（比照既有服務）
