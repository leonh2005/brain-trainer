"""執行單一任務：spawn claude -p，把結果寫回 DB。"""
import os
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
    cwd = task.get("cwd") or "."

    if not os.path.isdir(cwd):
        tq.set_status(conn, task_id, "failed",
                      error=f"工作目錄不存在：{cwd}", finished_at=time.time())
        return False, ""

    try:
        proc = subprocess.run(
            [claude_bin, "-p", task["spec"], *CLAUDE_ARGS],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        tq.set_status(conn, task_id, "failed",
                      error=f"逾時（超過 {timeout} 秒）", finished_at=time.time())
        return False, ""
    except FileNotFoundError:
        tq.set_status(conn, task_id, "failed",
                      error=f"找不到執行檔：{claude_bin}", finished_at=time.time())
        return False, ""

    out = (proc.stdout or "").strip()
    if proc.returncode == 0:
        tq.set_status(conn, task_id, "done", result=out,
                      error=None, finished_at=time.time())
        return True, out

    err = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
    tq.set_status(conn, task_id, "failed", error=err[:2000], finished_at=time.time())
    return False, out
