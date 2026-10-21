# -*- coding: utf-8 -*-
"""長工作的背景執行管理器：同種類同時只能跑一個，前端輪詢 /api/job_status。"""

import threading
import traceback
from datetime import datetime

JOBS = {
    "history": {"label": "更新歷史資料", "runner": None},
    "cyq": {"label": "重算 CYQ", "runner": None},
    "strategy": {"label": "跑策略選股", "runner": None},
    "pattern": {"label": "重算型態", "runner": None},
}

_LOCK = threading.Lock()
_STATE = {k: {"status": "idle", "progress": 0.0, "message": "尚未執行",
              "started_at": None, "finished_at": None} for k in JOBS}


def configure(history_runner, cyq_runner, strategy_runner, pattern_runner=None) -> None:
    JOBS["history"]["runner"] = history_runner
    JOBS["cyq"]["runner"] = cyq_runner
    JOBS["strategy"]["runner"] = strategy_runner
    JOBS["pattern"]["runner"] = pattern_runner


def _update(kind: str, **kw) -> None:
    with _LOCK:
        _STATE[kind].update(kw)


def _progress(kind: str, i: int, total: int, message: str) -> None:
    _update(kind, progress=round((i + 1) / max(total, 1), 3), message=message)


def start(kind: str) -> tuple:
    """回傳 (started, message)。執行中重複觸發會被拒絕。"""
    if kind not in JOBS:
        return False, f"未知的工作 {kind}"
    with _LOCK:
        if _STATE[kind]["status"] == "running":
            return False, "執行中"
        _STATE[kind].update(status="running", progress=0.0, message="開始…",
                            started_at=datetime.now().strftime("%H:%M:%S"), finished_at=None)
    runner = JOBS[kind]["runner"]
    if runner is None:
        _update(kind, status="error", message="未設定執行函式")
        return False, "未設定執行函式"

    def _run():
        try:
            message = runner(lambda i, n, m: _progress(kind, i, n, m)) or "完成"
            _update(kind, status="done", progress=1.0, message=str(message),
                    finished_at=datetime.now().strftime("%H:%M:%S"))
        except Exception as e:
            traceback.print_exc()
            _update(kind, status="error", message=str(e),
                    finished_at=datetime.now().strftime("%H:%M:%S"))

    threading.Thread(target=_run, daemon=True).start()
    return True, "已開始"


def snapshot() -> dict:
    with _LOCK:
        return {k: {**JOBS[k], **v, "runner": None} for k, v in _STATE.items()}
