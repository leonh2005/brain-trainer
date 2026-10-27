"""shioaji-gateway 的單元測試。

只測不碰網路的純邏輯：代號判斷、優先權鎖、秘密檔讀取、連線重試包裝。
所有 Shioaji API 呼叫都 mock 在函式邊界，不使用真實金鑰。
"""
import threading
import time
from types import SimpleNamespace

import app as gw
import pytest

# ── _is_candidate_code ──────────────────────────────────────────

@pytest.mark.parametrize("code", ["2330", "2454", "0050", "00929", "00878"])
def test_candidate_code_accepts_stocks_and_etfs(code):
    """4 碼個股與 5 碼數字 ETF 都算候選。"""
    assert gw._is_candidate_code(code) is True


def test_candidate_code_accepts_letter_suffix_etf():
    """00xxxX 字尾 ETF（如 00632R 反一）算候選。"""
    assert gw._is_candidate_code("00632R") is True


@pytest.mark.parametrize("code", ["233000", "123456", "IX0001", "", "ABCD"])
def test_candidate_code_rejects_others(code):
    """6 碼權證、指數、非數字代號都不算候選。"""
    assert gw._is_candidate_code(code) is False


def test_candidate_code_excludes_six_digit_etf():
    """記錄現況：純 6 碼 ETF（如 006208 富邦台50）會被當權證排除。

    實作只放行「4 碼數字」「5 碼數字」「00xxxX 字母字尾」三種；
    純數字 6 碼一律視為權證。若日後要納入 6 碼 ETF，需改 _is_candidate_code，
    這個測試會失敗提醒。
    """
    assert gw._is_candidate_code("006208") is False


# ── _PriorityLock ───────────────────────────────────────────────

def test_priority_lock_excludes_concurrent_holders():
    """第一個 acquire 成功後，第二個執行緒必須被擋住。"""
    lock = gw._PriorityLock()
    acquired = threading.Event()
    released = threading.Event()

    def holder():
        lock.acquire()
        acquired.set()
        released.wait(timeout=2)
        lock.release()

    def waiter():
        acquired.wait(timeout=2)
        lock.acquire()
        lock.release()

    t1 = threading.Thread(target=holder)
    t1.start()
    acquired.wait(timeout=2)

    t2 = threading.Thread(target=waiter, daemon=True)
    t2.start()
    t2.join(timeout=0.3)
    assert t2.is_alive(), "鎖被佔用時第二個執行緒不該取得"

    released.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert not t2.is_alive(), "釋放後第二個執行緒應可取得"


def test_priority_lock_serves_lower_number_first():
    """排隊中 priority 數字小的先取得（不是先到先赢）。"""
    lock = gw._PriorityLock()
    order = []

    # 先讓主執行緒持有鎖
    lock.acquire()

    def worker(priority, label):
        lock.acquire(priority=priority)
        order.append(label)
        lock.release()

    # 塞入順序：low(5) 先排、high(1) 後排
    threads = [
        threading.Thread(target=worker, args=(5, "low")),
        threading.Thread(target=worker, args=(1, "high")),
    ]
    for t in threads:
        t.start()
        time.sleep(0.05)  # 確保入列順序

    time.sleep(0.2)  # 讓兩個都進入等待佇列
    lock.release()

    for t in threads:
        t.join(timeout=3)

    assert order == ["high", "low"], f"應優先服務 priority=1，實際 {order}"


# ── _read_secret ────────────────────────────────────────────────

def test_read_secret_strips_whitespace(tmp_path, monkeypatch):
    """讀取秘密檔要去掉前後空白與換行。"""
    secret_file = tmp_path / "dummy_key.txt"
    secret_file.write_text("  fake-key-value\n", encoding="utf-8")
    monkeypatch.setattr(gw, "SECRETS_DIR", str(tmp_path))

    assert gw._read_secret("dummy_key.txt") == "fake-key-value"


def test_read_secret_missing_file_raises(tmp_path, monkeypatch):
    """檔案不存在時要拋錯，不能靜默回空字串。"""
    monkeypatch.setattr(gw, "SECRETS_DIR", str(tmp_path))

    with pytest.raises(FileNotFoundError):
        gw._read_secret("not_exist.txt")


# ── _run ────────────────────────────────────────────────────────

@pytest.fixture
def fake_conn(monkeypatch):
    """把模組級連線換成假的，避免任何真實登入。"""
    state = {"api": SimpleNamespace(name="first", logout=lambda: None)}
    monkeypatch.setattr(gw, "_conn", state)
    return state


def test_run_reuses_existing_connection(fake_conn):
    """已有連線時直接沿用，不重新登入。"""
    result = gw._run(lambda api: api.name)
    assert result == "first"


def test_run_relogins_once_on_failure(fake_conn, monkeypatch):
    """第一次呼叫失敗時要重登並重試一次，成功則回傳第二次結果。"""
    calls = []

    def fake_login():
        calls.append("login")
        return SimpleNamespace(name="second", logout=lambda: None)

    monkeypatch.setattr(gw, "_login", fake_login)

    def flaky(api):
        if api.name == "first":
            raise RuntimeError("連線失效")
        return api.name

    assert gw._run(flaky) == "second"
    assert calls == ["login"], "應只重登一次"


def test_run_clears_connection_after_double_failure(fake_conn, monkeypatch):
    """重試也失敗時，要把連線清成 None 並拋出例外。"""
    monkeypatch.setattr(
        gw, "_login", lambda: SimpleNamespace(name="second", logout=lambda: None)
    )

    def always_fail(api):
        raise RuntimeError("一直失敗")

    with pytest.raises(RuntimeError, match="一直失敗"):
        gw._run(always_fail)

    assert fake_conn["api"] is None, "連兩次失敗後連線應清空"
