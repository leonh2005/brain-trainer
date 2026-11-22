"""每來源獨立的去重狀態；原子寫入，失敗來源不寫。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cm_config import is_after_cutoff


def new_state(source: str) -> dict[str, Any]:
    return {
        "version": 1,
        "source": source,
        "baseline_done": False,
        "last_success_ts": None,
        "items": {},
    }


def load_state(path: str | Path) -> dict[str, Any]:
    if not Path(path).exists():
        return new_state(Path(path).stem.replace("seen_", ""))
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"state 檔損壞：{path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items", {}), dict):
        raise ValueError(f"state 檔格式錯誤：{path}")
    for key, default in new_state("").items():
        data.setdefault(key, default)
    return data


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, prefix=".tmp-", delete=False)
    try:
        json.dump(state, handle, ensure_ascii=False)
        handle.close()
        os.replace(handle.name, target)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def record_last_success(state: dict[str, Any], now_ts: int) -> None:
    state["last_success_ts"] = now_ts


def record_baseline(state: dict[str, Any], items: list[dict[str, Any]], now_ts: int) -> None:
    for item in items:
        state["items"][item["uid"]] = {
            "first_seen_ts": now_ts,
            "last_seen_ts": now_ts,
            "posted_ts": item.get("posted_ts"),
            "status": "baseline",
            "attempts": 0,
        }
    state["baseline_done"] = True
    record_last_success(state, now_ts)


def mark(
    state: dict[str, Any],
    uid: str,
    status: str,
    now_ts: int,
    message_id: int | None = None,
) -> None:
    entry = state["items"].setdefault(uid, {
        "first_seen_ts": now_ts, "posted_ts": None, "attempts": 0,
    })
    entry["status"] = status
    entry["last_seen_ts"] = now_ts
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if message_id is not None:
        entry["message_id"] = message_id


def select_new(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    max_attempts: int = 3,
) -> list[dict[str, Any]]:
    """回傳要通知的項目；不修改 state。"""
    if not state.get("baseline_done"):
        return []
    result = []
    for item in items:
        uid = item["uid"]
        entry = state["items"].get(uid)
        if entry is None:
            if is_after_cutoff(item.get("posted_ts")):
                result.append(item)
            continue
        if entry.get("status") in ("failed_retryable", "retryable") and int(entry.get("attempts", 0)) < max_attempts:
            result.append(item)
    return result


def needs_rebaseline(state: dict[str, Any], now_ts: int, gap_seconds: int) -> bool:
    last = state.get("last_success_ts")
    return bool(state.get("baseline_done") and last and now_ts - last > gap_seconds)
