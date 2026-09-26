import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

DEFAULT_TIMEOUT = 5.0

# 輸出裡「有沒有測試結果」的痕跡。**只在 run_pytest 用來判斷要不要改跑包裝過的
# test_code**（見 `_nothing_ran`），不再是成敗判準：stdout 是學習者自己也能寫的
# 通道，詳見 `VERDICT_PLUGIN_SRC`。
PYTEST_PASSED_RE = re.compile(r"(\d+) passed")
PYTEST_NOT_PASSED_RE = re.compile(r"(\d+) (failed|error|errors|skipped)")
# pytest 只收集測試函式；模組層級的 assert 不會被執行，一個測試都沒有。
TEST_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+test", re.M)
# 必須是第 0 欄的 assert，不能寫成 `^\s*assert`：縮排的 assert 代表它躲在某個
# 函式、類別或分支裡，包成測試函式之後同樣不會被執行——`def helper(): assert …`
# 包起來只會得到一個「什麼都沒驗證、卻一定會通過」的 test_auto，於是任何答案
# 都變成 correct。只認第 0 欄，才真的等於「模組層級的裸 assert」。
MODULE_ASSERT_RE = re.compile(r"^assert\b", re.M)


def _child_env(tmp, extra=None):
    """子行程的最小環境，只留啟動直譯器與輸出所需者。

    被執行的程式碼來自模型或學習者。繼承整個環境等於把本機的 token 與
    金鑰一併交出去（實測原本外洩 76 個變數，含
    CLAUDE_CODE_MESSAGING_TOKEN），而 HOME 指向真實家目錄，`.secrets`
    等憑證可被程式自行讀取。故 HOME/TMPDIR 改指暫存目錄，其餘不繼承。

    `extra` 只給執行器自己用（pytest 結果檔的路徑）。**這不是邊界**：外掛一載入
    就把該變數從 `os.environ` 移除，所以學習者的程式碼讀 `os.environ` 是空的，
    但行程的 envp 仍留著它（實測 `ps eww <pid>` 看得到），見 `VERDICT_PLUGIN_SRC`。

    這不是沙箱（無容器、無 seccomp、無資源限制）；第二道防線仍是既有的
    逾時與暫存目錄。
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tmp,
        "TMPDIR": tmp,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if extra:
        env.update(extra)
    return env


def _verdict_ok(returncode, verdict_path):
    """成敗只採信 pytest 自己寫下的結果檔。

    改版前是從 stdout 找 `N passed`。實測可被學習者偽造：pytest 的 fd-capture
    只是把原始 stdout dup 到別的 fd 並繼續開著，所以掃描 fd 3..39 逐一 os.write
    就能憑空印出 `1 passed in 0.01s` 騙過判定（`ok=True` → 記一筆 correct →
    算進掌握度）。標準輸出／標準錯誤都是子行程能寫的通道，不能當判準。

    結果檔改由外掛在 `pytest_sessionfinish` 寫出，路徑由執行器以環境變數傳入且
    位於學習者工作目錄之外。**檔案不存在一律視為未通過**：學習者若在測試中途
    `os._exit(0)`，pytest 來不及寫檔——而「來不及寫檔」正是「斷言一次都沒跑」。

    結束碼仍然保留為額外條件（`sys.exit(0)` 會讓 pytest 以 3 結束）。

    **這裡保證的只是「判準不再是 stdout/stderr」，不是「結果檔無法偽造」。**
    同一個 uid、同一個行程內的學習者程式碼仍可：(1) 從 `sys.modules` 或行程
    envp 取得外掛的路徑，在 `atexit` 裡覆寫結果檔；(2) 直接換掉外掛的
    `pytest_sessionfinish`。要擋住這一類攻擊只有把子行程隔離到另一個身分
    （容器／沙箱），不在本系統範圍內（見 `_child_env`）。相對改版前真正被關掉的
    是「偽造輸出」與「提早結束行程」——這兩條不需要知道任何路徑就成立。
    """
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if returncode != 0:
        return False
    if verdict.get("collected", 0) < 1:
        return False
    # 跳過的測試什麼都沒驗證，不算通過
    return bool(verdict.get("passed", 0)) and not (
        verdict.get("failed") or verdict.get("skipped"))


# 把成敗判準搬出子行程 stdout 的外掛。以 `-p` 注入（`python -m pytest` 會把
# 工作目錄放進 sys.path[0]，故寫在暫存目錄裡就載得到）。
VERDICT_PLUGIN_MODULE = "_learn_verdict_plugin"
VERDICT_PLUGIN_FILE = VERDICT_PLUGIN_MODULE + ".py"
VERDICT_PLUGIN_SRC = '''\
"""把 pytest 的結果寫到子行程工作目錄之外的一個檔案。生成檔，改動請改 executor.py。"""
import json
import os

# 一載入就從 os.environ 移除：test_solution.py 匯入 solution.py（學習者的程式碼）
# 發生在外掛載入之後，屆時 os.environ 已經沒有這個路徑（行程的 envp 仍有它，
# 見 executor._child_env——這不是安全邊界，只是擋掉最直覺的一步）。
_PATH = os.environ.pop("LEARN_PYTEST_RESULT", None)
_COUNTS = {"passed": 0, "failed": 0, "skipped": 0}


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _COUNTS["passed"] += 1
    elif report.failed:
        _COUNTS["failed"] += 1
    elif report.skipped:
        _COUNTS["skipped"] += 1


def pytest_sessionfinish(session, exitstatus):
    """測試全部跑完才會被呼叫；中途 os._exit(0) 的答案因此沒有結果檔。"""
    if not _PATH:
        return
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump({"collected": session.testscollected, **_COUNTS}, f)
'''


def _nothing_ran(stdout):
    """pytest 連一個測試的結果都沒報（不是「失敗」，是「根本沒跑」）。"""
    return not (PYTEST_PASSED_RE.search(stdout) or PYTEST_NOT_PASSED_RE.search(stdout))


def _bare_assert_script(test_code):
    """判斷 test_code 是不是「模組層級的裸 assert」而不是測試函式。

    這種寫法 pytest 一個測試都不會收集。它是提示詞的自然讀法（改版前的
    提示詞正是這樣寫的），所以值得包成測試函式再跑一次，否則每一題 write
    都會在建立時被 422 擋掉。

    但「沒有測試函式」本身不足以斷定：`x = 1` 這種沒有任何斷言的內容包起來
    也只會變成「什麼都沒驗證的測試」，會把它從 422 變成通過——那是更糟的
    誤判方向。故要求確實存在**第 0 欄**的 assert。

    「第 0 欄」不是吹毛求疵：`def helper(): assert …`、`class TestAdd:` 裡的方法、
    `if False:` 底下的 assert 都是第 0 欄以外，它們包進 test_auto 後仍然不會被
    執行，test_auto 卻會通過——那等於讓任何答案都判 correct。這種 test_code
    應該維持不通過（題目在建立時被 422 擋掉），不是被救回來。
    """
    if TEST_FUNC_RE.search(test_code):
        return False
    return bool(MODULE_ASSERT_RE.search(test_code))


def _wrap_as_test(test_code):
    """把模組層級的程式碼包成單一測試函式。"""
    body = textwrap.indent(textwrap.dedent(test_code), "    ")
    return f"def test_auto():\n{body}\n"


def _execute(files, argv, timeout, ok_fn=None, env_extra=None):
    """在獨立暫存目錄寫入檔案、執行 argv，環境層級的失敗一律轉成回傳值。

    執行環境本身的失敗（暫存目錄、寫檔、啟動子行程）不讓例外穿出，呼叫端
    才能從 `timed_out` 與 `env_error` 分辨「學習者寫錯」與「非預期錯誤」——
    後兩者都不可計入對錯，判成 wrong 會污染掌握度。

    `ok_fn(returncode, stdout)` 由呼叫端決定成敗怎麼認定，預設只比對結束碼。
    `env_extra` 併入子行程環境（見 `_child_env`）。
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
                    env=_child_env(tmp, extra=env_extra),
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


def _pytest_run(solution_code, test_code, timeout):
    """跑一次 pytest，判準是外掛寫出的結果檔（見 `_verdict_ok`）。

    結果檔放在另一個暫存目錄，刻意不在學習者的工作目錄底下（目錄名隨機，且
    子行程的 TMPDIR 指向它自己的工作目錄，不會從 TMPDIR 洩漏路徑）。
    """
    with tempfile.TemporaryDirectory(prefix="learn-verdict-") as verdict_dir:
        verdict_path = Path(verdict_dir, "verdict.json")
        return _execute(
            {
                "solution.py": solution_code,
                "test_solution.py": test_code,
                VERDICT_PLUGIN_FILE: VERDICT_PLUGIN_SRC,
            },
            [sys.executable, "-m", "pytest", "test_solution.py", "-q",
             "-p", "no:cacheprovider", "-p", VERDICT_PLUGIN_MODULE],
            timeout,
            ok_fn=lambda returncode, stdout: _verdict_ok(returncode, verdict_path),
            env_extra={"LEARN_PYTEST_RESULT": str(verdict_path)},
        )


def run_pytest(solution_code, test_code, timeout=DEFAULT_TIMEOUT):
    """以 pytest 執行學習者的 solution.py 對 test_solution.py。

    題目的 test_code 依定義是 pytest 斷言（見 tutor.QUESTION_PROMPT），必須
    真的交給 pytest 執行。當成純腳本跑的話 `def test_x(): ...` 只會被定義、
    從不被呼叫，斷言一次都沒跑，任何能定義出符號的答案都會以結束碼 0 判為
    正確——這是最糟的誤判方向，會灌爆掌握度。

    `ok` 只在「pytest 真的寫出結果檔、至少收集到一個測試、全部通過」時為
    True。結果檔缺席（學習者中途 os._exit(0)）或內容顯示有 failed/skipped 都
    不算通過；偽造子行程輸出不再有用（詳見 `_verdict_ok`）。

    模組層級的裸 assert（`from solution import add` 後直接 assert）pytest 不會
    收集，會讓每一題 write 在建立時被 422 擋掉。這種形狀只在「真的什麼都沒
    跑」且確實含有 assert 時，改包成單一測試函式再跑一次（見
    `_bare_assert_script`）；有測試函式的 test_code 一律原樣執行。

    判斷「什麼都沒跑」用的仍是輸出（`_nothing_ran`）。它只決定**改用哪一種
    題目形狀重跑一次**，重跑之後的成敗一樣只認結果檔，故偽造輸出頂多讓題目
    在建立時被 422，不會變成通過。
    """
    result = _pytest_run(solution_code, test_code, timeout)
    if (not result["ok"] and _nothing_ran(result["stdout"])
            and _bare_assert_script(test_code)):
        return _pytest_run(solution_code, _wrap_as_test(test_code), timeout)
    return result
