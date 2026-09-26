import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 5.0

# pytest 的結束碼本身不足以證明測試真的跑過。實測：學習者的程式碼在匯入時
# 呼叫 os._exit(0)，行程直接結束、pytest 來不及回報，結束碼就是 0；在 atexit
# 註冊 os._exit(0) 也能把「1 failed」洗成 0。故除了結束碼，還要求在輸出裡看到
# 真的通過的測試數，且沒有 failed/error/skipped——跳過的測試什麼都沒驗證。
PYTEST_PASSED_RE = re.compile(r"(\d+) passed")
PYTEST_NOT_PASSED_RE = re.compile(r"(\d+) (failed|error|errors|skipped)")


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


def _pytest_ok(returncode, stdout):
    """pytest 真的跑過至少一個測試且全部通過，才算成功。"""
    if returncode != 0:
        return False
    passed = PYTEST_PASSED_RE.search(stdout)
    if not passed or int(passed.group(1)) < 1:
        return False
    return not PYTEST_NOT_PASSED_RE.search(stdout)


def _execute(files, argv, timeout, ok_fn=None):
    """在獨立暫存目錄寫入檔案、執行 argv，環境層級的失敗一律轉成回傳值。

    執行環境本身的失敗（暫存目錄、寫檔、啟動子行程）不讓例外穿出，呼叫端
    才能從 `timed_out` 與 `env_error` 分辨「學習者寫錯」與「非預期錯誤」——
    後兩者都不可計入對錯，判成 wrong 會污染掌握度。

    `ok_fn(returncode, stdout)` 由呼叫端決定成敗怎麼認定，預設只比對結束碼。
    """
    decide = ok_fn or (lambda returncode, stdout: returncode == 0)
    try:
        with tempfile.TemporaryDirectory(prefix="learn-run-") as tmp:
            for name, content in files.items():
                Path(tmp, name).write_text(content, encoding="utf-8")
            try:
                proc = subprocess.run(
                    argv,
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
                    "env_error": False,
                }
            return {
                "ok": decide(proc.returncode, proc.stdout),
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "timed_out": False,
                "env_error": False,
            }
    except OSError as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"執行環境錯誤：{e}",
            "timed_out": False,
            "env_error": True,
        }


def run_python(code, timeout=DEFAULT_TIMEOUT):
    """在獨立暫存目錄執行一段程式碼，逾時即終止。

    只以行程結束碼判定成敗，適合「執行一個片段觀察行為」的題型（read 題的
    code_snippet）。拿它驗證測試是不夠的：學習者的答案只要 `sys.exit(0)` 或
    `os._exit(0)` 結束碼就是 0，斷言一次都沒跑也會被判成正確——批改 write 題
    請用 `run_pytest`。
    """
    return _execute({"solution.py": code}, [sys.executable, "solution.py"], timeout)


def run_pytest(solution_code, test_code, timeout=DEFAULT_TIMEOUT):
    """以 pytest 執行學習者的 solution.py 對 test_solution.py。

    題目的 test_code 依定義是 pytest 斷言（見 tutor.QUESTION_PROMPT），必須
    真的交給 pytest 執行。當成純腳本跑的話 `def test_x(): ...` 只會被定義、
    從不被呼叫，斷言一次都沒跑，任何能定義出符號的答案都會以結束碼 0 判為
    正確——這是最糟的誤判方向，會灌爆掌握度。

    `ok` 只在「至少一個測試被收集、全部通過」時為 True。實測結束碼：全部
    通過 0、有測試失敗 1、學習者程式碼匯入時語法錯誤 2、匯入時 SystemExit 3、
    未收集到任何測試 5；但結束碼不能單獨採信（見 PYTEST_PASSED_RE），
    故另外要求輸出中真的有通過的測試。
    """
    return _execute(
        {"solution.py": solution_code, "test_solution.py": test_code},
        [sys.executable, "-m", "pytest", "test_solution.py", "-q", "-p", "no:cacheprovider"],
        timeout,
        ok_fn=_pytest_ok,
    )
