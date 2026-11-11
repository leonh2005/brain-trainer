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
        return [SimpleNamespace(close=1.0, change_price=0.1, change_rate=0.1, total_amount=1.0)]

    api = SimpleNamespace(snapshots=snapshots)
    monkeypatch.setattr(gw, "_conn", {"api": api})
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: object())

    r = gw.app.test_client().get("/snapshot?codes=2330")
    assert r.status_code == 200
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
