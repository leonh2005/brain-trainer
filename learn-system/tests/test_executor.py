import os
import subprocess

from learn_system import executor
from learn_system.executor import run_python


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
    assert set(r) == {"ok", "stdout", "stderr", "timed_out"}
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
