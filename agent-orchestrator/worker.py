"""執行單一任務：spawn claude -p，把結果寫回 DB。"""
import os
import signal
import subprocess
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


def invoke_claude(prompt, cwd, claude_bin="claude", timeout=3600):
    """跑一次 claude -p；回傳 (returncode, stdout, stderr)。不碰 DB。

    returncode 為 -1 代表逾時（已終止整個 process group）；
    為 127 代表執行檔起不來。
    """
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


def run_task(conn, task, claude_bin="claude", timeout=3600):
    """執行一個任務；回傳 (ok, output)。無論成敗都會把狀態寫回 DB。"""
    task_id = task["id"]
    cwd = task.get("cwd")

    if not cwd or not os.path.isdir(cwd):
        _fail(conn, task, f"工作目錄不存在或未指定：{cwd}", retryable=False)
        return False, ""

    tq.set_status(conn, task_id, "running", worker_pid=os.getpid())
    code, out, err = invoke_claude(task["spec"], cwd, claude_bin, timeout)

    if code == -1:
        _fail(conn, task, err, retryable=True)
        return False, ""
    if code == 127:
        _fail(conn, task, err, retryable=False)
        return False, ""

    out = out.strip()
    if code == 0:
        tq.set_status(conn, task_id, "done", result=out,
                      error=None, finished_at=time.time())
        return True, out

    _fail(conn, task, err.strip() or f"exit code {code}", retryable=True)
    return False, out


def _fail(conn, task, error, retryable):
    """失敗收尾：可重試且未達上限 → 退回 pending 並排下次時間；否則標 failed。"""
    error = (error or "未知錯誤")[:2000]
    if retryable and task["retries"] < MAX_RETRIES:
        delay = BACKOFF_BASE * (2 ** task["retries"])
        tq.set_status(conn, task["id"], "pending",
                      retries=task["retries"] + 1,
                      next_attempt_at=time.time() + delay,
                      error=error, started_at=None, worker_pid=None)
    else:
        tq.set_status(conn, task["id"], "failed", error=error, finished_at=time.time())


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
