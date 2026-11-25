"""session 失效 / 重登韌性測試。

對應 2026-11-11 事故：Shioaji 每小時整點讓 token 失效，gateway 在持有全域鎖的
情況下重登，所有請求陪等 30-60 秒。這些測試鎖住「重登不擋路」與「快速失敗」。
"""
import sys
import threading
import time
from types import SimpleNamespace

import app as gw
import pytest


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(gw, "_conn", {"api": None})
    monkeypatch.setattr(gw, "_retired_apis", [])
    monkeypatch.setattr(gw, "_login_state", {
        "reconnecting": False, "failures": 0, "next_attempt_at": 0.0,
        "last_attempt": 0.0, "last_success": 0.0, "last_error": None, "generation": 0,
    })
    monkeypatch.setattr(gw, "_last_ok", {"ts": 0.0})
    monkeypatch.setattr(gw, "_last_session_down", {"ts": 0.0, "count": 0})
    monkeypatch.setattr(gw, "_login_lock", threading.Lock())
    monkeypatch.setattr(gw, "_state_cv", threading.Condition())
    monkeypatch.setattr(gw, "_lock", gw._PriorityLock())
    monkeypatch.setattr(gw, "_bidask_subscribed", {})
    monkeypatch.setattr(gw, "_bidask_cache", {})
    yield


# ── 鎖逾時 ──────────────────────────────────────────────────────

def test_lock_timeout_removes_entry():
    lock = gw._PriorityLock()
    held = threading.Event()
    release = threading.Event()

    def holder():
        lock.acquire()
        held.set()
        release.wait(2)
        lock.release()

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(2)

    with pytest.raises(gw.LockTimeout):
        lock.acquire(timeout=0.1)
    assert lock.stats()["queue_depth"] == 0, "逾時的 ticket 留在佇列裡會永久死鎖"

    release.set()
    t.join(2)
    lock.acquire(timeout=0.5)
    lock.release()


def test_timed_out_head_does_not_block_later_waiter():
    """排頭逾時後，下一個等待者必須馬上被服務（用 heappop 會拔錯人）。"""
    lock = gw._PriorityLock()
    lock.acquire()                      # 先佔住
    release = threading.Event()
    got = []

    def waiter(priority, timeout=None):
        try:
            lock.acquire(priority, timeout=timeout)
        except gw.LockTimeout:
            return
        got.append(priority)
        lock.release()

    head = threading.Thread(target=waiter, args=(5, 0.1))   # 先排，逾時
    head.start()
    time.sleep(0.02)
    later = threading.Thread(target=waiter, args=(1, None))  # 後排，不逾時
    later.start()
    head.join(2)
    time.sleep(0.15)
    lock.release()                      # 放掉初始持有
    later.join(2)

    assert got == [1]


# ── session down ────────────────────────────────────────────────

def test_session_down_clears_current_api():
    api = object()
    gw._conn["api"] = api
    gw._handle_session_down(id(api))
    assert gw._conn["api"] is None
    assert gw._last_session_down["count"] == 1


def test_stale_session_down_does_not_clear_new_api():
    old, new = object(), object()
    gw._conn["api"] = new
    gw._handle_session_down(id(old))     # 舊 session 的延遲回呼
    assert gw._conn["api"] is new, "舊回呼不能清掉新 session"


def test_session_down_handler_does_not_take_data_lock():
    """handler 跑在 SDK 的 Rust 執行緒上；若它取 _lock 會跟等回覆的執行緒互鎖。"""
    held = threading.Event()
    release = threading.Event()

    def holder():
        gw._lock.acquire()
        held.set()
        release.wait(1)
        gw._lock.release()

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(2)

    api = object()
    gw._conn["api"] = api
    t0 = time.monotonic()
    gw._handle_session_down(id(api))
    elapsed = time.monotonic() - t0

    release.set()
    t.join(2)
    assert elapsed < 0.05


# ── 重登不擋路（事故根因回歸）────────────────────────────────────

def test_login_does_not_hold_global_lock(monkeypatch):
    started = threading.Event()

    def slow_login():
        started.set()
        time.sleep(0.6)
        return object()

    monkeypatch.setattr(gw, "_login", slow_login)
    errors = []

    def worker():
        try:
            gw._run(lambda api: "ok")
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(2)

    t0 = time.monotonic()
    gw._lock.acquire(timeout=0.3)
    elapsed = time.monotonic() - t0
    gw._lock.release()
    assert elapsed < 0.3, "登入期間不該持有資料鎖"

    t.join(3)
    assert errors == []


def test_concurrent_requests_trigger_single_login(monkeypatch):
    count = {"n": 0}

    def slow_login():
        count["n"] += 1
        time.sleep(0.4)
        return object()

    monkeypatch.setattr(gw, "_login", slow_login)
    results, errors = [], []

    def worker():
        try:
            results.append(gw._run(lambda api: "ok"))
        except Exception as e:      # noqa: BLE001
            errors.append(type(e).__name__)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert count["n"] == 1, "重登必須 single-flight"
    assert results == ["ok"] * 10
    assert errors == []


def test_reconnect_wait_is_capped(monkeypatch):
    """重連期間的請求最多等 RECONNECT_WAIT_S，不能一路等到整個請求預算用完。"""
    gw._conn["api"] = None
    gw._login_state["reconnecting"] = True
    t0 = time.monotonic()
    with pytest.raises(gw.GatewayReconnecting):
        gw._run(lambda api: "ok", budget_s=12)
    assert time.monotonic() - t0 < 3.5


def test_retry_after_relogin_succeeds(monkeypatch):
    api1, api2 = object(), object()
    monkeypatch.setattr(gw, "_conn", {"api": api1})
    monkeypatch.setattr(gw, "_login", lambda: api2)
    seen = []

    def fn(api):
        seen.append(api)
        if api is api1:
            raise ConnectionError("session dead")
        return "ok"

    assert gw._run(fn) == "ok"
    assert seen == [api1, api2]


def test_business_error_does_not_relogin(monkeypatch):
    api = object()
    monkeypatch.setattr(gw, "_conn", {"api": api})
    logins = []
    monkeypatch.setattr(gw, "_login", lambda: logins.append(1))

    def fn(_api):
        raise ValueError("bad param")

    with pytest.raises(ValueError):
        gw._run(fn)
    assert logins == []


# ── 逾時與錯誤格式 ──────────────────────────────────────────────

def test_snapshot_passes_timeout(monkeypatch):
    recorded = {}

    def snapshots(contracts, timeout=None):
        recorded["timeout"] = timeout
        return [SimpleNamespace(
            code="2330", close=1.0, change_price=0.1, change_rate=0.1,
            total_amount=1.0, total_volume=1, yesterday_volume=1,
            buy_volume=1, sell_volume=1, high=1.0, low=1.0, open=1.0,
        )]

    api = SimpleNamespace(snapshots=snapshots)
    monkeypatch.setattr(gw, "_conn", {"api": api})
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: object())

    r = gw.app.test_client().get("/snapshot?codes=2330")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True, f"快照.endpoint 不該因假資料缺欄位而整批失敗：{r.get_json()}"
    assert recorded["timeout"] == gw.SNAPSHOT_TIMEOUT_MS


def test_bidask_subscribe_has_no_timeout(monkeypatch):
    calls = {}

    class Q:
        def subscribe(self, contract, **kw):
            calls.update(kw)

    api = SimpleNamespace(quote=Q())
    monkeypatch.setattr(gw, "_conn", {"api": api})
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: object())
    fake = SimpleNamespace(constant=SimpleNamespace(
        QuoteType=SimpleNamespace(BidAsk=1), QuoteVersion=SimpleNamespace(v1=1)))
    monkeypatch.setitem(sys.modules, "shioaji", fake)

    gw.app.test_client().get("/bidask?code=2330")
    assert "timeout" not in calls, "subscribe 不吃 timeout 參數"


def test_busy_error_is_http_200(monkeypatch):
    def boom(*a, **k):
        raise gw.GatewayBusy("busy")

    monkeypatch.setattr(gw, "_run", boom)
    r = gw.app.test_client().get("/snapshot?codes=2330")
    assert r.status_code == 200, "sim-invest 用 raise_for_status，錯誤必須維持 200"
    assert r.get_json()["ok"] is False


def test_market_snapshot_aborts_after_consecutive_failures(monkeypatch):
    calls = {"n": 0}

    def snapshots(batch, timeout=None):
        calls["n"] += 1
        raise TimeoutError("dead session")

    contracts = [SimpleNamespace(code=str(1000 + i)) for i in range(1000)]  # 每批 200 → 5 批
    api = SimpleNamespace(
        Contracts=SimpleNamespace(Stocks=SimpleNamespace(TSE=contracts, OTC=[])),
        snapshots=snapshots,
    )
    monkeypatch.setattr(gw, "_conn", {"api": api})

    r = gw.app.test_client().get("/market_snapshot")
    assert r.status_code == 200
    assert r.get_json()["ok"] is False
    assert calls["n"] == 3, "連續 3 批失敗就要放棄，不能跑滿整個市場"


def test_market_snapshot_keeps_partial_data(monkeypatch):
    """已抓到的批次要保留並標 partial，不能因為後段失敗就整個端點掛掉。"""
    calls = {"n": 0}

    def snapshots(batch, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [SimpleNamespace(code="1000", close=10.0, high=10.5, low=9.5,
                                    change_rate=1.0, total_volume=100)]
        raise TimeoutError("dead")

    contracts = [SimpleNamespace(code=str(1000 + i)) for i in range(1000)]
    api = SimpleNamespace(
        Contracts=SimpleNamespace(Stocks=SimpleNamespace(TSE=contracts, OTC=[])),
        snapshots=snapshots,
    )
    monkeypatch.setattr(gw, "_conn", {"api": api})

    d = gw.app.test_client().get("/market_snapshot").get_json()
    assert d["ok"] is True
    assert d["partial"] is True
    assert len(d["items"]) == 1
    assert calls["n"] == 4      # 1 成功 + 連續 3 次失敗後停手


# ── health ─────────────────────────────────────────────────────

def test_health_reports_state(monkeypatch):
    client = gw.app.test_client()

    gw._conn["api"] = object()
    d = client.get("/health").get_json()
    assert (d["status"], d["state"], d["logged_in"]) == ("ok", "ok", True)
    assert "queue_depth" in d and "generation" in d

    gw._conn["api"] = None
    gw._login_state["reconnecting"] = True
    d = client.get("/health").get_json()
    assert d["state"] == "reconnecting"
    assert d["status"] == "ok", "status 欄位不可改，既有 consumer 會看它"


def test_bidask_old_session_cache_not_served(monkeypatch):
    old, new = object(), object()
    monkeypatch.setattr(gw, "_conn", {"api": new})
    gw._bidask_subscribed["2330"] = time.time()   # 已訂閱，走快取分支
    gw._bidask_cache["2330"] = {"ts": time.time(), "api_id": id(old),
                                "bids": [], "asks": []}
    d = gw.app.test_client().get("/bidask?code=2330").get_json()
    assert d["ok"] is False
    assert d["error"] == "訂閱中，請稍後重新查詢"


# ── 連線清理（2026-11-16 451 Too Many Connections 事故）──────────

def test_session_down_retires_old_api():
    """session down 要保留舊 api 參考，不能直接丟掉（否則連線洩漏）。"""
    api = SimpleNamespace(logout=lambda: None)
    gw._conn["api"] = api
    gw._handle_session_down(id(api))
    assert gw._conn["api"] is None
    assert gw._retired_apis == [api]
    assert gw._last_session_down["count"] == 1


def test_stale_session_down_does_not_retire_current_api():
    """舊 session 的延遲回呼不可把新 api 放進退休清單。"""
    old = SimpleNamespace(logout=lambda: None)
    new = SimpleNamespace(logout=lambda: None)
    gw._conn["api"] = new
    gw._handle_session_down(id(old))
    assert gw._retired_apis == []
    assert gw._conn["api"] is new


def test_relogin_logout_retired_apis(monkeypatch):
    """_relogin 必須在登入前 logout 所有退休 session。"""
    logged_out = []
    retired = [SimpleNamespace(logout=lambda i=i: logged_out.append(i)) for i in range(3)]
    monkeypatch.setattr(gw, "_retired_apis", retired)
    new_api = SimpleNamespace(name="new", logout=lambda: None)
    monkeypatch.setattr(gw, "_login", lambda: new_api)

    # prior_api=None 代表走 session-down 路徑
    assert gw._relogin(time.monotonic() + 5, None) is new_api
    assert logged_out == [0, 1, 2]
    assert gw._retired_apis == [], "取出後要清空，避免下次重複 logout"


def test_relogin_does_not_logout_prior_api_twice(monkeypatch):
    """prior_api 同時在退休清單時，只能 logout 一次。"""
    calls = []
    old = SimpleNamespace(
        logout=lambda: calls.append("logout"),
        clear_session_down_callback=lambda: None,
        clear_on_tick_stk_v1_callback=lambda: None,
    )
    monkeypatch.setattr(gw, "_retired_apis", [old])
    monkeypatch.setattr(gw, "_login", lambda: SimpleNamespace(logout=lambda: None))
    gw._relogin(time.monotonic() + 5, old)
    assert calls == ["logout"]


def test_login_failure_logout_partial_api(monkeypatch):
    """api.login() 失敗時要 logout 半登入的 api，避免 Solace 連線洩漏。"""
    monkeypatch.setenv("SHIOAJI_API_KEY", "fake-key")
    monkeypatch.setenv("SHIOAJI_SECRET_KEY", "fake-secret")
    calls = []

    def fail_login(**kw):
        raise RuntimeError("451 Too Many Connections")

    partial = SimpleNamespace(
        login=fail_login,
        logout=lambda: calls.append("logout"),
    )
    monkeypatch.setitem(sys.modules, "shioaji", SimpleNamespace(Shioaji=lambda **kw: partial))

    with pytest.raises(RuntimeError, match="451"):
        gw._login()
    assert calls == ["logout"], "登入失敗也要關掉本次建立的 api"


# ── 慢查詢逾時不得觸發重登（2026-11-17 開盤 storm）──────────────

@pytest.mark.parametrize("url", [
    "/daily_ohlcv?code=2330&days=5",
    "/intraday?code=2330",
    "/kbars?code=2330&days=5",
    "/big_orders?code=2330",
    "/scanner?type=pct&count=5",
    "/market_snapshot",
])
def test_slow_query_timeout_does_not_mark_down(monkeypatch, url):
    """慢查詢逾時只回錯，不可 mark_down → 重登（開盤 meas 40→50 storm 教訓）。"""
    monkeypatch.setattr(gw, "_conn", {"api": SimpleNamespace(logout=lambda: None)})
    captured = {}

    def fake_run(fn, **kw):
        captured.update(kw)
        raise TimeoutError("Timeout Topic: api/v1/data/kbars, Timeout: 14037ms")

    monkeypatch.setattr(gw, "_run", fake_run)

    r = gw.app.test_client().get(url)
    assert r.status_code == 200
    assert r.get_json().get("ok") is False
    assert captured.get("mark_down_on_timeout") is False, f"{url} 必須關閉逾時重登"
