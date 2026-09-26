import os
import subprocess

from learn_system import executor
from learn_system.executor import run_pytest, run_python

TEST_CODE = "from solution import add\ndef test_add():\n    assert add(1, 2) == 3"
CORRECT = "def add(a, b):\n    return a + b"
WRONG = "def add(a, b):\n    return a - b"


def test_successful_run():
    r = run_python("print('hello')")
    assert r["ok"] is True
    assert r["stdout"].strip() == "hello"
    assert r["timed_out"] is False


def test_runtime_error_is_not_ok():
    r = run_python("raise ValueError('boom')")
    assert r["ok"] is False
    assert "ValueError" in r["stderr"]


def test_syntax_error_is_not_ok():
    r = run_python("def broken(:")
    assert r["ok"] is False
    assert r["stderr"] != ""


def test_infinite_loop_times_out():
    r = run_python("while True: pass", timeout=0.5)
    assert r["timed_out"] is True
    assert r["ok"] is False


def test_reading_stdin_does_not_hang():
    # stdin 關閉，input() 會立刻 EOFError 而非永久卡住
    r = run_python("input()", timeout=0.5)
    assert r["timed_out"] is False
    assert r["ok"] is False
    assert "EOFError" in r["stderr"]


def test_stdin_is_devnull_for_the_child(monkeypatch):
    """移除 stdin=subprocess.DEVNULL 時，只有這個測試會失敗。

    test_reading_stdin_does_not_hang 做不到：pytest 底下 fd 0 本身就是個
    EOF 檔，子行程即使沿用繼承的 stdin 也照樣拿到 EOFError，
    因此只能直接檢查傳給 subprocess 的參數。
    """
    captured = {}
    real_run = subprocess.run

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(executor.subprocess, "run", spy)
    run_python("print('x')")
    assert captured["stdin"] is subprocess.DEVNULL


def test_non_utf8_output_does_not_raise():
    # 子行程輸出非 UTF-8 位元組時，不能讓 UnicodeDecodeError 穿出。
    r = run_python("import sys; sys.stdout.buffer.write(bytes([255, 254]))")
    assert set(r) == {"ok", "stdout", "stderr", "timed_out", "env_error"}
    assert isinstance(r["stdout"], str)
    # 子行程正常結束（returncode 0），依規格 ok 應為 True。
    assert r["ok"] is True
    assert r["timed_out"] is False


def test_infrastructure_error_returns_dict(monkeypatch):
    # 執行環境失敗要回傳 dict（非預期錯誤不計入對錯），不能拋例外。
    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(executor.subprocess, "run", boom)
    r = run_python("print('hello')")
    assert r["ok"] is False
    assert r["timed_out"] is False
    # 呼叫端靠這個旗標把「環境出錯」與「學習者寫錯」分開，兩者都不計入對錯
    assert r["env_error"] is True
    assert "執行環境錯誤" in r["stderr"]


def test_assertion_failure_reported():
    r = run_python("assert 1 == 2, 'nope'")
    assert r["ok"] is False
    assert "nope" in r["stderr"]


def test_child_does_not_inherit_parent_environment(monkeypatch):
    # 被執行的程式碼來自模型/學習者，繼承整個環境等於把本機 token 一起交出去
    monkeypatch.setenv("LEARN_SYSTEM_SECRET_TOKEN", "leak-me")
    r = run_python("import os; print(os.environ.get('LEARN_SYSTEM_SECRET_TOKEN', 'ABSENT'))")
    assert "ABSENT" in r["stdout"]
    assert "leak-me" not in r["stdout"]


def test_child_home_is_not_the_real_home():
    # HOME 若指向真實家目錄，程式碼可自行讀取 ~/.secrets 等憑證
    r = run_python("import os; print(os.environ.get('HOME'))")
    assert r["stdout"].strip() != os.path.expanduser("~")


def test_child_keeps_path_so_subprocesses_work():
    r = run_python("import os; print(bool(os.environ.get('PATH')))")
    assert "True" in r["stdout"]


def test_run_pytest_grades_by_test_outcome():
    assert run_pytest(CORRECT, TEST_CODE)["ok"] is True
    assert run_pytest(WRONG, TEST_CODE)["ok"] is False


def test_run_pytest_cannot_be_fooled_by_exiting_the_process():
    """結束碼不足以判定成敗：這些答案的行程結束碼都是 0。

    `sys.exit(0)` 其實會被 pytest 判成 INTERNALERROR（結束碼 3），但
    `os._exit(0)` 直接結束行程、pytest 來不及回報，結束碼就是 0；
    在 atexit 註冊 os._exit(0) 更能把「1 failed」洗成 0。故 ok 必須另外
    確認輸出裡真的有通過的測試。
    """
    for answer in ("import sys\nsys.exit(0)",
                   "import os\nos._exit(0)",
                   "raise SystemExit(0)",
                   "import atexit, os\natexit.register(lambda: os._exit(0))\n" + WRONG):
        assert run_pytest(answer, TEST_CODE)["ok"] is False, answer


def test_run_pytest_requires_a_collected_test():
    # 沒有斷言可跑時 pytest 結束碼是 5（"no tests ran"），不能算通過。
    # 這裡也是「裸 assert 包裝」的邊界：內容沒有任何 assert 的 test_code
    # 不會被包成測試函式，否則 422 會變成通過。空字串同理。
    assert run_pytest(CORRECT, "from solution import add\nx = 1")["ok"] is False
    assert run_pytest(CORRECT, "")["ok"] is False
    # 被跳過的測試什麼都沒驗證，同樣不算通過
    assert run_pytest(CORRECT, "import pytest\ndef test_x():\n    pytest.skip('nope')")["ok"] is False


def test_run_pytest_handles_bare_module_level_asserts():
    """pytest 只收集測試函式；模組層級的 assert 要包成測試函式才會被執行。"""
    bare = "from solution import add\nassert add(1, 2) == 3"
    assert run_pytest(CORRECT, bare)["ok"] is True
    assert run_pytest(WRONG, bare)["ok"] is False


def test_bare_assert_detection_distinguishes_junk_from_asserts():
    """包裝只適用於「第 0 欄有 assert 的裸腳本」。

    沒有 assert 可言的 test_code 包起來只會變成「什麼都沒驗證的測試」，
    那就把 422 變成了通過——比擋掉更糟。
    """
    assert executor._bare_assert_script("from solution import add\nassert add(1, 2) == 3")
    # 已經是測試函式：原樣執行，不能包（包了內層測試函式不會被收集）
    assert not executor._bare_assert_script(TEST_CODE)
    # 沒有任何斷言：不包，維持不通過
    assert not executor._bare_assert_script("from solution import add\nx = 1")
    assert not executor._bare_assert_script("")


def test_bare_assert_detection_rejects_nested_asserts():
    """縮排的 assert 不算數，否則會開出一條新的假通過路徑。

    這些 assert 全都躲在某個函式、類別或死分支裡，包進 test_auto 之後依然
    不會被執行，但 test_auto 本身會「通過」——於是任何答案都變成 correct。
    這種 test_code 必須維持不通過（題目建立時被 422 擋掉）。
    """
    for test_code in (
        "from solution import add\n\ndef helper():\n    assert add(1, 2) == 3",
        "from solution import add\n\nclass TestAdd:\n    def check(self):\n        assert add(1, 2) == 3",
        "from solution import add\nif False:\n    assert False",
        "from solution import add\nif True:\n    assert add(1, 2) == 3",
    ):
        assert not executor._bare_assert_script(test_code), test_code


def test_run_pytest_does_not_rescue_nested_asserts():
    # 沒被執行的 assert 不是判準，不能因為它存在就判通過
    nested = "from solution import add\n\ndef helper():\n    assert add(1, 2) == 3"
    assert run_pytest(WRONG, nested)["ok"] is False
    assert run_pytest(CORRECT, nested)["ok"] is False


def test_run_pytest_bare_asserts_are_still_not_fooled_by_exiting():
    # 包裝路徑必須沿用同一套結束碼防線
    bare = "from solution import add\nassert add(1, 2) == 3"
    assert run_pytest("import os\nos._exit(0)", bare)["ok"] is False


def test_run_pytest_times_out():
    r = run_pytest("while True: pass", TEST_CODE, timeout=0.5)
    assert r["timed_out"] is True
    assert r["ok"] is False


def test_run_pytest_child_does_not_inherit_parent_environment(monkeypatch):
    # run_pytest 必須沿用同一套環境淨化，否則學習者程式碼照樣讀得到金鑰
    monkeypatch.setenv("LEARN_SYSTEM_SECRET_TOKEN", "leak-me")
    solution = ("import os\ndef leak():\n"
                "    return os.environ.get('LEARN_SYSTEM_SECRET_TOKEN', 'ABSENT')")
    r = run_pytest(solution, "from solution import leak\ndef test_leak():\n    assert leak() == 'ABSENT'")
    assert r["ok"] is True
