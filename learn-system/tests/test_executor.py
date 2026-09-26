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


def test_assertion_failure_reported():
    r = run_python("assert 1 == 2, 'nope'")
    assert r["ok"] is False
    assert "nope" in r["stderr"]
