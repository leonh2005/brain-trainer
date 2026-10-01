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


def run_task(conn, task, claude_bin="claude", timeout=3600):
    """執行一個任務；回傳 (ok, output)。無論成敗都會把狀態寫回 DB。"""
    task_id = task["id"]
    cwd = task.get("cwd")

    if not cwd or not os.path.isdir(cwd):
        tq.set_status(conn, task_id, "failed",
                      error=f"工作目錄不存在或未指定：{cwd}", finished_at=time.time())
        return False, ""

    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", task["spec"], *CLAUDE_ARGS],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        tq.set_status(conn, task_id, "failed",
                      error=f"無法啟動 {claude_bin}：{exc}", finished_at=time.time())
        return False, ""

    tq.set_status(conn, task_id, "running", worker_pid=proc.pid)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        tq.set_status(conn, task_id, "failed",
                      error=f"逾時（超過 {timeout} 秒）", finished_at=time.time())
        return False, ""

    out = (stdout or "").strip()
    if proc.returncode == 0:
        tq.set_status(conn, task_id, "done", result=out,
                      error=None, finished_at=time.time())
        return True, out

    err = (stderr or "").strip() or f"exit code {proc.returncode}"
    tq.set_status(conn, task_id, "failed", error=err[:2000], finished_at=time.time())
    return False, out


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
