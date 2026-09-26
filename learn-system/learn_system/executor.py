import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 5.0


def run_python(code, timeout=DEFAULT_TIMEOUT):
    """在獨立暫存目錄執行學習者的程式碼，逾時即終止。"""
    with tempfile.TemporaryDirectory(prefix="learn-run-") as tmp:
        script = Path(tmp) / "solution.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "ok": False,
                "stdout": (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                "stderr": (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                "timed_out": True,
            }
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
