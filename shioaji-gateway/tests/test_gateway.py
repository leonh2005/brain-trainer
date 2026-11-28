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
    monkeypatch.setattr(gw, "_retired_apis", [])
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


# ── _scanner_kind ───────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("pct", "ChangePercentRank"),
    ("volume", "VolumeRank"),
    ("amount", "AmountRank"),
    ("", "ChangePercentRank"),          # 未指定 → 沿用舊行為（漲跌幅）
    ("PCT", "ChangePercentRank"),       # 大小寫不敏感
])
def test_scanner_kind_maps_query_values(value, expected):
    """查詢字串要用簡單字（pct/volume/amount）對應到 Shioaji 的列舉名稱。"""
    assert gw._scanner_kind(value) == expected


def test_scanner_kind_rejects_unknown_value():
    """未知類型要明確報錯，不能靜默退回漲跌幅（免得畫面顯示錯的排行）。"""
    with pytest.raises(ValueError):
        gw._scanner_kind("turnover")


# ── /scanner 路由參數驗證 ───────────────────────────────────────

def test_scanner_route_returns_json_error_for_bad_count():
    """count 非數字要回 {ok:false} JSON，而不是未捕捉例外造成的 500 HTML。"""
    with gw.app.test_client() as client:
        resp = client.get("/scanner?count=abc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False


# ── /snapshot 代號對位 ────────────────────────────────────────────

def _fake_snapshot(code: str, close: float) -> SimpleNamespace:
    return SimpleNamespace(
        code=code, close=close, change_price=0.0, change_rate=0.0,
        total_amount=0.0, total_volume=0, yesterday_volume=0,
        buy_volume=0, sell_volume=0, high=close, low=close, open=close,
    )


def test_snapshot_skips_evicted_code_without_shifting_later_prices(monkeypatch):
    """SDK 省略被淘汰的快照時，不能被 zip 對位把後面代號的價格套到前面。"""
    monkeypatch.setattr(gw, "_resolve_contract", lambda api, code: SimpleNamespace(
        code=code, name=f"name-{code}", reference=100.0,
        limit_up=110.0, limit_down=90.0))

    # 2026-11-25 09:45 真實事故：2303 快取鍵被淘汰，SDK 只回 2356（38.7）與 2883（30.25）
    snaps = [_fake_snapshot("2356", 38.7), _fake_snapshot("2883", 30.25)]

    class FakeAPI:
        def snapshots(self, contracts, timeout):
            return snaps

    monkeypatch.setattr(gw, "_run", lambda work, **kwargs: work(FakeAPI()))

    with gw.app.test_client() as client:
        resp = client.get("/snapshot?codes=2303,2356,2883")

    data = resp.get_json()["data"]
    assert set(data) == {"2356", "2883"}, f"缺價代號不該拿到別人的價格：{data}"
    assert data["2356"]["close"] == 38.7
    assert data["2883"]["close"] == 30.25


# ── /positions 與權限例外 ────────────────────────────────────────

def test_permission_errors_are_not_conn_errors():
    """權限/簽署例外重登一萬次也不會好，卻因 MRO 含 OSError 被當成連線錯誤 →
    每次 /positions 都打斷共用 session（2026-11-28 CRITICAL）。"""
    import shioaji as sj

    assert gw._is_conn_error(sj.ShioajiPermissionError("denied")) is False
    assert gw._is_conn_error(sj.AccountNotSignError("not signed")) is False
    # 真正的連線錯誤仍要重登
    assert gw._is_conn_error(sj.AuthError("auth failed")) is True


def test_positions_without_stock_account_raises_business_error(monkeypatch):
    """stock_account 為 None 時丟 ValueError（業務錯誤），不得觸發重登。"""
    executed = {}

    def fake_run(work, **kwargs):
        executed.update(kwargs)
        return work(SimpleNamespace(stock_account=None))

    monkeypatch.setattr(gw, "_run", fake_run)

    with gw.app.test_client() as client:
        resp = client.get("/positions")

    body = resp.get_json()
    assert body["ok"] is False
    assert "no stock account" in body["error"]
    assert executed.get("retry") is False


def test_positions_passes_sdk_timeout(monkeypatch):
    """/positions 必須顯式傳 timeout，否則 SDK 預設 30 秒會一路佔住共用鎖。
    也必須指定 Unit.Share，否則 quantity 是「張」，市值會少算 1000 倍。"""
    seen = {}

    class FakeAPI:
        stock_account = SimpleNamespace()

        def list_positions(self, account, unit=None, timeout=None):
            seen["timeout"] = timeout
            seen["unit"] = unit
            return [SimpleNamespace(code="006208", name="富邦台50", quantity=1000,
                                    price=150.0, last_price=208.0, pnl=58000.0,
                                    cond="Cash")]

    monkeypatch.setattr(gw, "_run", lambda work, **kwargs: work(FakeAPI()))

    with gw.app.test_client() as client:
        resp = client.get("/positions")

    assert resp.get_json()["ok"] is True
    assert seen["timeout"] == gw.POSITIONS_TIMEOUT_MS
    assert str(seen["unit"]).endswith("Share")
    assert resp.get_json()["data"][0]["code"] == "006208"
