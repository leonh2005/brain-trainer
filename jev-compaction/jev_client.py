#!/usr/bin/env python3
"""TypeSafe Jev client：對一批文字問「是否仍與當前任務相關」，回傳機率。

只依賴標準庫（urllib），不引入 requests——proxy 的依賴越少越好。

格式依 @typesafe-ai/sdk 0.6.0 的實作：
  POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <key>
  body: {"state": ..., "questions": {...}, "model": "jev-latest"}
  response: {"answers": {"out0": {"type":"noul","noul":0.05}}, "usage": {...}}
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_KEY_PATH = Path.home() / "CCProject/.secrets/typesafe_jev_key.txt"

# 單批候選數。compact_test.mjs 實測 25 筆一批在速度與成本間最合適。
BATCH_SIZE = 25


class JevError(RuntimeError):
    """Jev 呼叫失敗。呼叫端應據此 fail-open（送原請求）。"""


def load_api_key(key_path: Path | str | None = None) -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    path = Path(key_path or DEFAULT_KEY_PATH)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise JevError(f"找不到 TypeSafe API key：{path}") from e


def _post(body: dict, api_key: str, timeout: float, base_url: str = API_BASE) -> dict:
    req = urllib.request.Request(
        base_url + ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise JevError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise JevError(f"{type(e).__name__}: {e}") from e


def score_relevance(items: list[tuple[str, str]], task: str, api_key: str,
                    timeout: float = 10.0, model: str = DEFAULT_MODEL,
                    base_url: str = API_BASE) -> dict[str, float]:
    """對一批 (id, text) 問「是否仍與 task 相關」。

    回傳 {id: 機率}，機率 1 = 仍相關。失敗時丟 JevError，由呼叫端決定怎麼退。
    """
    if not items:
        return {}

    outputs = "\n\n".join(f"【{i}】{text}" for i, (_id, text) in enumerate(items))
    state = {
        "current_task": task,
        "tool_outputs": f"以下是對話中較早產生的工具輸出：\n\n{outputs}",
    }
    questions = {
        f"out{i}": {
            "type": "noul",
            "instructions": (
                f"state.tool_outputs 裡編號【{i}】的工具輸出，"
                "是否仍是回答 state.current_task 所必要的資訊？"
            ),
            "criteria": {
                "true": "仍是回答該問題所需的資訊",
                "false": "與回答該問題無關，可以移除",
            },
        }
        for i in range(len(items))
    }

    data = _post({"state": state, "questions": questions, "model": model}, api_key, timeout, base_url)

    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise JevError(f"回應缺少 answers：{json.dumps(data)[:200]}")

    out: dict[str, float] = {}
    for i, (item_id, _text) in enumerate(items):
        ans = answers.get(f"out{i}")
        if not isinstance(ans, dict) or "noul" not in ans:
            raise JevError(f"回應缺少 out{i}.noul")
        out[item_id] = float(ans["noul"])
    return out


def usage_of(data: dict) -> dict:
    return data.get("usage") or {}
