"""把一個大任務拆解成子任務。"""
import json
import os
import re
import time

import taskqueue as tq
import worker

PLAN_PROMPT = """你是任務規劃器。把下面這個任務拆解成 2 到 5 個子任務。

任務：{spec}

可用的工作目錄：{cwd}

規則：
- 每個子任務都要有完整、可獨立執行的描述（不要寫「同上」這種）
- 若某個子任務需要其他子任務先完成，用 depends_on 標明（填子任務索引，從 0 開始）
- 彼此獨立的子任務不要互相依賴，讓它們能並行
- 子任務要能被安全地重複執行（系統可能因失敗而重跑），避免不可逆的副作用

只輸出 JSON，不要任何其他文字或解說：
{{"subtasks": [{{"title": "短標題", "spec": "完整描述", "cwd": "{cwd}", "depends_on": []}}]}}
"""


RETRY = 2
ORCH_RETRY_DELAY = 5  # 秒


def _invoke_with_retry(prompt, cwd, claude_bin, timeout, attempts=RETRY):
    """呼叫 claude，失敗（code != 0）時重試；回傳最後一次的 (code, out, err)。"""
    last = None
    for i in range(attempts + 1):
        last = worker.invoke_claude(prompt, cwd, claude_bin, timeout)
        if last[0] == 0:
            return last
        if i < attempts:
            time.sleep(ORCH_RETRY_DELAY)
    return last


def parse_subtasks(text):
    """從 claude 的輸出取出 subtasks 清單；格式不對就丟 ValueError。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"拆解結果不是合法 JSON：{exc}") from exc
    subs = data.get("subtasks") if isinstance(data, dict) else None
    if not isinstance(subs, list) or not subs:
        raise ValueError("拆解結果缺少有效的 subtasks 陣列")

    n = len(subs)
    for i, sub in enumerate(subs):
        if not isinstance(sub, dict):
            raise ValueError(f"子任務 {i} 不是物件：{sub!r}")
        if not str(sub.get("spec") or sub.get("title") or "").strip():
            raise ValueError(f"子任務 {i} 缺少 spec 或 title")
        deps = sub.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValueError(f"depends_on 不是陣列：{deps!r}")
        for d in deps:
            # bool 是 int 的子類，True/False 要擋掉
            if isinstance(d, bool) or not isinstance(d, int) or d < 0 or d >= n:
                raise ValueError(f"depends_on 索引越界：{d!r}")
            if d == i:
                raise ValueError(f"子任務 {i} 依賴自己")
    _check_acyclic(subs)
    return subs


def _check_acyclic(subs):
    """depends_on 不得形成循環（循環會讓子任務永遠挑不到）。"""
    n = len(subs)
    state = [0] * n  # 0=未訪, 1=訪問中, 2=完成

    def visit(i):
        if state[i] == 1:
            raise ValueError(f"depends_on 形成循環（涉及子任務 {i}）")
        if state[i] == 2:
            return
        state[i] = 1
        for d in subs[i].get("depends_on") or []:
            visit(d)
        state[i] = 2

    for i in range(n):
        visit(i)


def decompose(conn, parent, claude_bin="claude", timeout=600):
    """呼叫 claude 拆解 parent，建立 blocked 子任務，parent 轉 blocked。回傳子任務 id 清單。"""
    cwd = parent.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        tq.set_status(conn, parent["id"], "failed",
                      error=f"工作目錄不存在或未指定：{cwd}", finished_at=time.time())
        return []
    prompt = PLAN_PROMPT.format(spec=parent["spec"], cwd=cwd)
    code, out, err = _invoke_with_retry(prompt, cwd, claude_bin, timeout)

    if code != 0:
        tq.set_status(conn, parent["id"], "failed",
                      error=(err.strip() or f"拆解失敗 exit={code}")[:2000],
                      finished_at=time.time())
        return []

    try:
        subs = parse_subtasks(out)
    except Exception as exc:  # 模型輸出千奇百怪，一律收斂成可診斷的 failed
        detail = (out or "").strip()[:500]
        tq.set_status(conn, parent["id"], "failed",
                      error=f"拆解結果無法解析：{exc}｜原始輸出：{detail}"[:2000],
                      finished_at=time.time())
        return []

    try:
        conn.execute("BEGIN IMMEDIATE")
        id_by_index = {}
        for i, sub in enumerate(subs):
            # 一次就寫成 blocked + parent_id：不留「先 pending」的可搶窗口，
            # 也保證中途失敗時整批 rollback、不會留下孤兒
            id_by_index[i] = tq.add_task(
                conn,
                title=sub.get("title") or f"子任務 {i + 1}",
                spec=sub.get("spec") or sub.get("title") or "",
                cwd=sub.get("cwd") or cwd,
                source=parent.get("source", "cli"),
                status="blocked",
                parent_id=parent["id"],
            )
        for i, sub in enumerate(subs):  # 依賴要等 id 都建好才能映射
            deps = [id_by_index[d] for d in (sub.get("depends_on") or [])]
            tq.set_status(conn, id_by_index[i], "blocked", depends_on=json.dumps(deps))
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        tq.set_status(conn, parent["id"], "failed",
                      error=f"建立子任務失敗：{exc}"[:2000], finished_at=time.time())
        return []

    tq.set_status(conn, parent["id"], "blocked", finished_at=None)
    return list(id_by_index.values())


def children_of(conn, parent_id):
    return [t for t in tq.list_tasks(conn) if t.get("parent_id") == parent_id]


def confirm(conn, parent_id):
    """把某個 orchestrator 任務的 blocked 子任務放行為 pending。回傳放行數。"""
    parent = tq.get_task(conn, parent_id)
    if not parent:
        raise ValueError(f"找不到任務：{parent_id}")
    if parent.get("kind") != "orchestrator":
        raise ValueError(f"任務 {parent_id} 不是 orchestrator 任務")
    n = 0
    for child in children_of(conn, parent_id):
        if child["status"] == "blocked":
            tq.set_status(conn, child["id"], "pending")
            n += 1
    return n


SUMMARIZE_PROMPT = """以下是一個大任務拆解後、各子任務的執行結果。請用繁體中文寫一段簡短總結（5 行內）：完成了什麼、有沒有要注意的地方。

原始任務：{spec}

{parts}
"""


def summarize(conn, parent, claude_bin="claude", timeout=600):
    """彙整子任務結果寫回母任務。任何子任務 failed → 母任務 failed。回傳是否成功。"""
    children = children_of(conn, parent["id"])
    failed = [c for c in children if c["status"] == "failed"]
    if failed:
        names = "、".join(c["title"] for c in failed)
        tq.set_status(conn, parent["id"], "failed",
                      error=f"有子任務失敗：{names}"[:2000], finished_at=time.time())
        return False

    parts = "\n\n".join(
        f"### {c['title']}\n{(c.get('result') or '(無產出)')[:2000]}" for c in children
    )
    cwd = parent.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        tq.set_status(conn, parent["id"], "failed",
                      error=f"工作目錄不存在或未指定：{cwd}", finished_at=time.time())
        return False
    prompt = SUMMARIZE_PROMPT.format(spec=parent["spec"], parts=parts)
    code, out, err = _invoke_with_retry(prompt, cwd, claude_bin, timeout)
    if code != 0:
        tq.set_status(conn, parent["id"], "failed",
                      error=(err.strip() or f"彙整失敗 exit={code}")[:2000],
                      finished_at=time.time())
        return False

    tq.set_status(conn, parent["id"], "done", result=out.strip(),
                  error=None, finished_at=time.time())
    return True
