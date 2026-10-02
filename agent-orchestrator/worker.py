"""執行單一任務：spawn claude -p，把結果寫回 DB。"""
import json
import os
import signal
import subprocess
import threading
import time

import taskqueue as tq

CLAUDE_ARGS = [
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
    "--disallowedTools", "Agent,Workflow",
    "--output-format", "text",
]

MAX_RETRIES = 2
BACKOFF_BASE = 30  # 秒；退避為 BACKOFF_BASE * 2**retries
# 秒；閒置超過此秒數沒有輸出即判定卡住。刻意拉長：一條跑超過 10 分鐘的單一
# tool call（測試、下載、推論）中途不會有任何 stream 輸出，太短會誤殺正常任務。
IDLE_TIMEOUT = 1800

# 去掉 CLAUDE_ARGS 末尾的 ["--output-format", "text"]，換成 stream-json
STREAM_ARGS = [*CLAUDE_ARGS[:-2], "--output-format", "stream-json", "--verbose"]


def invoke_claude(prompt, cwd, claude_bin="claude", timeout=3600, idle_timeout=None):
    """跑一次 claude -p；回傳 (returncode, stdout, stderr)。不碰 DB。

    idle_timeout 給定時改用 stream-json 逐行讀取，超過該秒數沒有新輸出即殺掉整個
    process group 並回 -1。其他情況 -1 代表總時長逾時、127 代表執行檔起不來。
    """
    if idle_timeout is None:
        return _invoke_text(prompt, cwd, claude_bin, timeout)
    return _invoke_streaming(prompt, cwd, claude_bin, timeout, idle_timeout)


def _invoke_text(prompt, cwd, claude_bin, timeout):
    """--output-format text：一次收完。"""
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", prompt, *CLAUDE_ARGS],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return 127, "", f"無法啟動 {claude_bin}：{exc}"

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return -1, "", f"逾時（超過 {timeout} 秒）"
    return proc.returncode, stdout or "", stderr or ""


def _invoke_streaming(prompt, cwd, claude_bin, timeout, idle_timeout):
    """stream-json：逐行讀取以偵測進展；回傳最終 result 文字。"""
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", prompt, *STREAM_ARGS],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return 127, "", f"無法啟動 {claude_bin}：{exc}"

    state = {"last": time.time(), "result": None, "is_error": False}

    def _reader():
        for line in proc.stdout:
            state["last"] = time.time()  # 收到任何一行都算有進展
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                state["result"] = ev.get("result") or ""
                state["is_error"] = bool(ev.get("is_error"))

    err_lines = []

    def _read_err():
        for line in proc.stderr:
            err_lines.append(line)

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    # stderr 必須同時排空：滿了會讓子行程卡在 write、stdout 再也不出東西
    threading.Thread(target=_read_err, daemon=True).start()

    started = time.time()
    while proc.poll() is None:
        time.sleep(0.5)
        now = time.time()
        if now - state["last"] > idle_timeout:
            _kill_tree(proc)
            return -1, "", f"閒置逾時（超過 {idle_timeout} 秒沒有輸出）"
        if now - started > timeout:
            _kill_tree(proc)
            return -1, "", f"逾時（超過 {timeout} 秒）"

    reader.join(timeout=5)
    if state["result"] is None:
        detail = "".join(err_lines).strip()[:500]
        return 1, "", f"claude 結束但沒有 result 事件｜stderr：{detail}"
    if state["is_error"]:
        return 1, state["result"], "claude 回報 is_error"
    return 0, state["result"], ""


def run_task(conn, task, claude_bin="claude", timeout=3600, idle_timeout=None):
    """執行一個任務；回傳 (ok, output)。無論成敗都會把狀態寫回 DB。"""
    task_id = task["id"]
    cwd = task.get("cwd")
    expected = task.get("started_at")  # 這次執行的識別；被收割後就不該再寫入

    if not cwd or not os.path.isdir(cwd):
        _fail(conn, task, f"工作目錄不存在或未指定：{cwd}", retryable=False,
              expected_started=expected)
        return False, ""

    _finish(conn, task_id, expected, "running", worker_pid=os.getpid())
    code, out, err = invoke_claude(task["spec"], cwd, claude_bin, timeout,
                                   idle_timeout=idle_timeout)

    if code == -1:
        _fail(conn, task, err, retryable=True, expected_started=expected)
        return False, ""
    if code == 127:
        _fail(conn, task, err, retryable=False, expected_started=expected)
        return False, ""

    out = out.strip()
    if code == 0:
        _finish(conn, task_id, expected, "done", result=out,
                error=None, finished_at=time.time())
        return True, out

    _fail(conn, task, err.strip() or f"exit code {code}", retryable=True,
          expected_started=expected)
    return False, out


def _finish(conn, task_id, expected_started, status, **fields):
    """寫入狀態。若任務已被收割／改過就不覆蓋（expected_started 為 None 時無條件寫）。"""
    if expected_started is not None:
        tq.finish_if_running(conn, task_id, expected_started, status, **fields)
        return
    tq.set_status(conn, task_id, status, **fields)


def _fail(conn, task, error, retryable, expected_started=None):
    """失敗收尾：可重試且未達上限 → 退回 pending 並排下次時間；否則標 failed。
    任務若已被收割／改過則不覆蓋。"""
    error = (error or "未知錯誤")[:2000]
    if retryable and task["retries"] < MAX_RETRIES:
        delay = BACKOFF_BASE * (2 ** task["retries"])
        _finish(conn, task["id"], expected_started, "pending",
                retries=task["retries"] + 1,
                next_attempt_at=time.time() + delay,
                error=error, started_at=None, worker_pid=None)
    else:
        _finish(conn, task["id"], expected_started, "failed",
                error=error, finished_at=time.time())


def _kill_tree(proc):
    """殺掉整個 process group（含子、孫行程）：先 SIGTERM，逾時再 SIGKILL。"""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
