import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 5.0


def run_python(code, timeout=DEFAULT_TIMEOUT):
    """在獨立暫存目錄執行學習者的程式碼，逾時即終止。

    執行環境本身的失敗（暫存目錄、寫檔、啟動子行程）一律轉成回傳值，
    不讓例外穿出，呼叫端才能區分「學習者寫錯」與「環境出錯」。
    """
    try:
        with tempfile.TemporaryDirectory(prefix="learn-run-") as tmp:
            script = Path(tmp) / "solution.py"
            script.write_text(code, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, str(script)],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                    cwd=tmp,
                )
            except subprocess.TimeoutExpired as e:
                return {
                    "ok": False,
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                    "timed_out": True,
                }
            return {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "timed_out": False,
            }
    except OSError as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"執行環境錯誤：{e}",
            "timed_out": False,
        }
