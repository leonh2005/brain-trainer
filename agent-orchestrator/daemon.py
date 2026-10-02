"""常駐排程：收割逾時任務、認領可跑的任務、交給 worker。"""
import os
import sys
import threading
import time

import notify
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


_SUMMARIZING = set()  # 已在背景彙整中的母任務 id（避免重複派工）


def _summarize_thread(db_path, parent_id, claude_bin, timeout):
    conn = tq.connect(db_path)
    try:
        parent = tq.get_task(conn, parent_id)
        if parent:
            orchestrator.summarize(conn, parent, claude_bin=claude_bin, timeout=timeout)
            _notify_result(conn, parent_id)  # 彙整完成也要推（否則手機看不到報告）
    finally:
        conn.close()
        _SUMMARIZING.discard(parent_id)


def finalize_parents(conn, db_path, claude_bin="claude", timeout=3600):
    """收尾 blocked orchestrator 母任務：任一子任務失敗即收尾；全部完成才彙整。

    彙整是 30–120 秒的 LLM 呼叫，丟到背景跑 —— 否則主迴圈會停擺、並行形同關閉。
    回傳本輪派出的彙整數。"""
    done = 0
    for parent in tq.list_tasks(conn, status="blocked"):
        if parent.get("kind") != "orchestrator":
            continue
        if parent["id"] in _SUMMARIZING:
            continue  # 已經在背景彙整中
        children = orchestrator.children_of(conn, parent["id"])
        if not children:
            continue  # 還沒拆解
        has_failed = any(c["status"] == "failed" for c in children)
        all_done = all(c["status"] == "done" for c in children)
        if not (has_failed or all_done):
            continue
        if has_failed:
            # 依賴失敗者的手足會永遠 pending（claim_next 不放行），母任務就永遠卡 blocked；
            # 先把這些孤兒標掉（快，留在主執行緒），再背景彙整。
            for c in children:
                if c["status"] in ("pending", "blocked"):
                    tq.set_status(conn, c["id"], "failed",
                                  error="前置任務失敗，已跳過", finished_at=time.time())
        _SUMMARIZING.add(parent["id"])
        threading.Thread(target=_summarize_thread,
                         args=(db_path, parent["id"], claude_bin, timeout),
                         daemon=True).start()
        done += 1
    return done


def tick(conn, claude_bin="claude", timeout=3600):
    """同步跑一輪：收割逾時 → 認領並執行一個任務。回傳執行數（0 或 1）。

    只供測試與單任務模式；常駐請用 main_loop（它另外負責收尾母任務，且 worker 走
    stream-json／idle-timeout、彙整走背景）。"""
    reap_timed_out(conn, timeout)

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


MAX_PARALLEL = 3


def _notify(line):
    """送推播；失敗要留下痕跡（否則「手機很安靜」無從診斷），但絕不拋。"""
    try:
        if not notify.send(line):
            print(f"[daemon] 推播失敗：{line[:80]}", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"[daemon] 推播例外：{exc!r}", file=sys.stderr, flush=True)


def _notify_result(conn, task_id):
    """任務結束、或母任務剛拆解完時推播；任何失敗都不影響任務本身。"""
    try:
        task = tq.get_task(conn, task_id)
        if not task:
            return
        status = task["status"]
        if status in ("done", "failed"):
            icon = "✅" if status == "done" else "❌"
            line = f"{icon} {task['title']}（{task['id']}）"
            if task["error"]:
                line += f"\n{task['error'][:500]}"
            elif task["result"]:
                line += f"\n{task['result'][:1500]}"
        elif task.get("kind") == "orchestrator" and status == "blocked":
            n = len(orchestrator.children_of(conn, task_id))
            line = (f"🧩 {task['title']}（{task['id']}）已拆解成 {n} 個子任務，"
                    f"看過後 /confirm {task['id']} 放行")
        else:
            return
        _notify(line)
    except Exception:
        pass


def _run_worker(db_path, task_id, claude_bin, timeout):
    """背景 thread 的進入點：用自己的 DB 連線跑一個任務。"""
    conn = tq.connect(db_path)
    try:
        task = tq.get_task(conn, task_id)
        if not task:
            return
        if task.get("kind") == "orchestrator":
            orchestrator.decompose(conn, task, claude_bin=claude_bin, timeout=timeout)
        else:
            worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout,
                            idle_timeout=worker.IDLE_TIMEOUT)
    except Exception as exc:  # thread 不可因單次失敗而死，否則任務卡在 running 佔名額
        print(f"[daemon] worker {task_id} 失敗：{exc!r}", file=sys.stderr, flush=True)
        try:
            tq.set_status(conn, task_id, "failed",
                          error=f"worker 例外：{exc}"[:2000], finished_at=time.time())
        except Exception:
            pass
    finally:
        _notify_result(conn, task_id)  # 例外路徑（worker 崩潰）也要推播
        conn.close()


def start_ready(conn, db_path, claude_bin="claude", timeout=3600, parallel=MAX_PARALLEL):
    """把可跑的任務填滿到 parallel 上限，背景執行。回傳本輪啟動的 task id 清單。"""
    running = len(tq.list_tasks(conn, status="running"))
    slots = max(0, parallel - running)
    started = []
    for _ in range(slots):
        task = tq.claim_next(conn)
        if not task:
            break
        threading.Thread(target=_run_worker,
                         args=(db_path, task["id"], claude_bin, timeout),
                         daemon=True).start()
        started.append(task["id"])
    return started


def _acquire_singleton(db_path):
    """確保同一個佇列只有一支 daemon —— 否則兩支各自數 running，實際並行會翻倍。"""
    lock = db_path + ".daemon.lock"
    if os.path.exists(lock):
        try:
            pid = int(open(lock).read().strip())
        except (OSError, ValueError):
            pid = None
        if pid:
            try:
                os.kill(pid, 0)  # 還活著
            except ProcessLookupError:
                pass  # 舊的、已死 → 接手
            except OSError:
                pass
            else:
                raise RuntimeError(f"已有 daemon 在跑（pid {pid}）")
    with open(lock, "w") as fh:
        fh.write(str(os.getpid()))


def main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db",
              parallel=MAX_PARALLEL):
    _acquire_singleton(db_path)
    conn = tq.connect(db_path)
    tq.init_db(conn)
    while True:
        try:
            reap_timed_out(conn, timeout)
            finalize_parents(conn, db_path, claude_bin=claude_bin, timeout=timeout)
            start_ready(conn, db_path, claude_bin=claude_bin, timeout=timeout,
                        parallel=parallel)
        except Exception as exc:  # 常駐服務不可因單輪失敗而死
            print(f"[daemon] 迴圈失敗：{exc!r}", file=sys.stderr, flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    main_loop()
