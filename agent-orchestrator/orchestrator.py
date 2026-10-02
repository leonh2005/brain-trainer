"""把一個大任務拆解成子任務。"""
import json
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

只輸出 JSON，不要任何其他文字或解說：
{{"subtasks": [{{"title": "短標題", "spec": "完整描述", "cwd": "{cwd}", "depends_on": []}}]}}
"""


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
    for sub in subs:
        deps = sub.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValueError(f"depends_on 不是陣列：{deps!r}")
        for d in deps:
            if not isinstance(d, int) or d < 0 or d >= n:
                raise ValueError(f"depends_on 索引越界：{d!r}")
    return subs


def decompose(conn, parent, claude_bin="claude", timeout=600):
    """呼叫 claude 拆解 parent，建立 blocked 子任務，parent 轉 blocked。回傳子任務 id 清單。"""
    cwd = parent.get("cwd") or "."
    prompt = PLAN_PROMPT.format(spec=parent["spec"], cwd=cwd)
    code, out, err = worker.invoke_claude(prompt, cwd, claude_bin, timeout)

    if code != 0:
        tq.set_status(conn, parent["id"], "failed",
                      error=(err.strip() or f"拆解失敗 exit={code}")[:2000],
                      finished_at=time.time())
        return []

    try:
        subs = parse_subtasks(out)
    except ValueError as exc:
        tq.set_status(conn, parent["id"], "failed",
                      error=f"拆解結果無法解析：{exc}"[:2000], finished_at=time.time())
        return []

    id_by_index = {}
    for i, sub in enumerate(subs):
        tid = tq.add_task(
            conn,
            title=sub.get("title") or f"子任務 {i + 1}",
            spec=sub.get("spec") or sub.get("title") or "",
            cwd=sub.get("cwd") or cwd,
            source=parent.get("source", "cli"),
        )
        tq.set_status(conn, tid, "blocked", parent_id=parent["id"])
        id_by_index[i] = tid

    for i, sub in enumerate(subs):  # 依賴要等 id 都建好才能映射
        deps = [id_by_index[d] for d in (sub.get("depends_on") or [])]
        tq.set_status(conn, id_by_index[i], "blocked", depends_on=json.dumps(deps))

    tq.set_status(conn, parent["id"], "blocked", finished_at=None)
    return list(id_by_index.values())
