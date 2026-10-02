"""常駐排程：收割逾時任務、認領可跑的任務、交給 worker。"""
import sys
import time

import orchestrator
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


def finalize_parents(conn, claude_bin="claude", timeout=3600):
    """收尾 blocked orchestrator 母任務：任一子任務失敗即收尾；全部完成才彙整。回傳處理數。"""
    done = 0
    for parent in tq.list_tasks(conn, status="blocked"):
        if parent.get("kind") != "orchestrator":
            continue
        children = orchestrator.children_of(conn, parent["id"])
        if not children:
            continue  # 還沒拆解
        if any(c["status"] == "failed" for c in children):
            # 依賴失敗者的手足會永遠 pending（claim_next 不放行），母任務就永遠卡 blocked；
            # 這裡把它們標掉，也讓 ls 誠實。
            for c in children:
                if c["status"] in ("pending", "blocked"):
                    tq.set_status(conn, c["id"], "failed",
                                  error="前置任務失敗，已跳過", finished_at=time.time())
            orchestrator.summarize(conn, parent, claude_bin=claude_bin, timeout=timeout)
            done += 1
        elif all(c["status"] == "done" for c in children):
            orchestrator.summarize(conn, parent, claude_bin=claude_bin, timeout=timeout)
            done += 1
    return done


def tick(conn, claude_bin="claude", timeout=3600):
    """跑一輪：收割逾時 → 收尾已完成的母任務 → 認領並執行一個任務。"""
    reap_timed_out(conn, timeout)
    finalize_parents(conn, claude_bin=claude_bin, timeout=timeout)

    task = tq.claim_next(conn)
    if not task:
        return 0
    if task.get("kind") == "orchestrator":
        orchestrator.decompose(conn, task, claude_bin=claude_bin, timeout=timeout)
    else:
        worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout)
    return 1


def _tick_safe(conn, **kwargs):
    """包住 tick：任何意外例外都吞掉並回 0，讓常駐迴圈活著。"""
    try:
        return tick(conn, **kwargs)
    except Exception as exc:  # 常駐服務不可因單輪失敗而死
        print(f"[daemon] tick 失敗：{exc!r}", file=sys.stderr, flush=True)
        return 0


def main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db"):
    conn = tq.connect(db_path)
    tq.init_db(conn)
    while True:
        if _tick_safe(conn, claude_bin=claude_bin, timeout=timeout) == 0:
            time.sleep(poll)


if __name__ == "__main__":
    main_loop()
