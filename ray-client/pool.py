"""Ray 資源池的最小客戶端：提交、收集、記錄使用狀況。"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import ray

LOG_FILE = os.path.expanduser("~/CCProject/logs/ray_usage.log")
TPE = timezone(timedelta(hours=8))
EXPECTED_RAY = "2.40.0"


def _write(record: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def connect() -> dict[str, Any]:
    ray.init("ray://127.0.0.1:10001", log_to_driver=False)
    version = ray.__version__
    if version != EXPECTED_RAY:
        raise RuntimeError(f"Ray 版本不符：本機 {version}，預期 {EXPECTED_RAY}")
    return dict(ray.cluster_resources())


def submit(func: Callable, argslist: list[tuple], name: str) -> list[Any]:
    source = inspect.getsource(func)
    code_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    remote_func = ray.remote(func)
    refs = [remote_func.remote(*args) for args in argslist]
    _write({
        "ts": datetime.now(TPE).isoformat(),
        "event": "submit",
        "name": name,
        "tasks": len(refs),
        "code_sha12": code_hash,
    })
    return refs


def collect(refs: list[Any], name: str, started: float) -> list[Any] | None:
    error = None
    try:
        results = ray.get(refs)
        status = "ok"
    except Exception as exc:
        results = None
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    _write({
        "ts": datetime.now(TPE).isoformat(),
        "event": "finish",
        "name": name,
        "tasks": len(refs),
        "duration_s": round(time.monotonic() - started, 3),
        "status": status,
        "error": error,
    })
    return results


def shutdown() -> None:
    ray.shutdown()
