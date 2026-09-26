import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 5.0


def _child_env(tmp):
    """子行程的最小環境，只留啟動直譯器與輸出所需者。

    被執行的程式碼來自模型或學習者。繼承整個環境等於把本機的 token 與
    金鑰一併交出去（實測原本外洩 76 個變數，含
    CLAUDE_CODE_MESSAGING_TOKEN），而 HOME 指向真實家目錄，`.secrets`
    等憑證可被程式自行讀取。故 HOME/TMPDIR 改指暫存目錄，其餘不繼承。

    這不是沙箱（無容器、無 seccomp、無資源限制）；第二道防線仍是既有的
    逾時與暫存目錄。
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tmp,
        "TMPDIR": tmp,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


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
                    env=_child_env(tmp),
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
