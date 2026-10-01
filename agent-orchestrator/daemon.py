"""常駐排程：收割逾時任務、認領可跑的任務、交給 worker。"""
import time

import taskqueue as tq
import worker


def reap_timed_out(conn, timeout):
    """把 running 但 started_at 超過 timeout 的任務標 failed。回傳收割數。"""
    now = time.time()
    n = 0
    for task in tq.list_tasks(conn, status="running"):
        started = task["started_at"]
        if started is not None and now - started > timeout:
            tq.set_status(conn, task["id"], "failed",
                          error=f"逾時（超過 {timeout} 秒，由 daemon 收割）",
                          finished_at=now)
            n += 1
    return n


def tick(conn, claude_bin="claude", timeout=3600):
    """跑一輪：先收割逾時，再認領並執行一個任務。回傳本輪執行的任務數（0 或 1）。"""
    reap_timed_out(conn, timeout)
    task = tq.claim_next(conn)
    if not task:
        return 0
    worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout)
    return 1


def main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db"):
    conn = tq.connect(db_path)
    tq.init_db(conn)
    while True:
        if tick(conn, claude_bin=claude_bin, timeout=timeout) == 0:
            time.sleep(poll)


if __name__ == "__main__":
    main_loop()
