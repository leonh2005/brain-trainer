"""tick 中繼端點與回呼的單元測試。

對應 2026-11-12 事故：intraday_monitor 自己登入 Shioaji 與 gateway 互踢 session。
這些測試鎖住「回呼不取資料鎖」「resume_seq 不漏 tick」「refcount 不亂退訂」。
"""
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import app as gw
import pytest


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(gw, "_conn", {"api": None})
    monkeypatch.setattr(gw, "_login_state", {
        "reconnecting": False, "failures": 0, "next_attempt_at": 0.0,
        "last_attempt": 0.0, "last_success": 0.0, "last_error": None, "generation": 3,
    })
    monkeypatch.setattr(gw, "_last_ok", {"ts": 0.0})
    monkeypatch.setattr(gw, "_last_session_down", {"ts": 0.0, "count": 0})
    monkeypatch.setattr(gw, "_login_lock", threading.Lock())
    monkeypatch.setattr(gw, "_state_cv", threading.Condition())
    monkeypatch.setattr(gw, "_lock", gw._PriorityLock())
    monkeypatch.setattr(gw, "_tick_lock", threading.Lock())
    monkeypatch.setattr(gw, "_tick_seq", {})
    monkeypatch.setattr(gw, "_tick_ring", {})
    monkeypatch.setattr(gw, "_tick_meta", {})
    monkeypatch.setattr(gw, "_tick_clients", {})
    yield


def _fake_api():
    return SimpleNamespace(name="fake-api")


def _fake_contract(code="2330"):
    return SimpleNamespace(code=code, name="台積電", reference=1000.0,
                           limit_up=1100.0, limit_down=900.0)


def _fake_tick(code="2330", ts="2026-11-12T09:00:01.000000", close=100.0,
               volume=2, tick_type=1, simtrade=False):
    return SimpleNamespace(code=code, datetime=datetime.fromisoformat(ts),
                           close=str(close), volume=str(volume),
                           tick_type=str(tick_type), simtrade=simtrade)


def _client():
    return gw.app.test_client()


# ── 回呼 ────────────────────────────────────────────────────────

def test_callback_appends_and_filters_simtrade(monkeypatch):
    api = _fake_api()
    monkeypatch.setattr(gw, "_conn", {"api": api})
    cb = gw._make_on_tick(api)

    cb("TSE", _fake_tick(simtrade=True))
    assert gw._tick_seq.get("2330") is None

    cb("TSE", _fake_tick())
    assert gw._tick_seq["2330"] == 1
    assert gw._tick_ring["2330"][-1] == (1, "2026-11-12T09:00:01.000000", 100.0, 2, 1)


def test_callback_drops_stale_session(monkeypatch):
    old_api, new_api = _fake_api(), _fake_api()
    monkeypatch.setattr(gw, "_conn", {"api": new_api})
    cb = gw._make_on_tick(old_api)
    cb("TSE", _fake_tick())
    assert gw._tick_seq.get("2330") is None


def test_callback_does_not_take_data_lock(monkeypatch):
    """SDK Rust 執行緒取 _lock 會跟等 SDK 回覆的請求互鎖。"""
    api = _fake_api()
    monkeypatch.setattr(gw, "_conn", {"api": api})
    cb = gw._make_on_tick(api)
    gw._lock.acquire()
    try:
        t = time.monotonic()
        cb("TSE", _fake_tick())
        assert (time.monotonic() - t) < 0.05
    finally:
        gw._lock.release()


# ── /ticks 輪詢 ─────────────────────────────────────────────────

def _seed(code, seqs):
    gw._tick_seq[code] = max(seqs)
    gw._tick_ring[code] = gw.deque(
        (s, f"2026-11-12T09:00:{s:02d}.000000", 100.0 + s, 1, 1) for s in seqs)
    gw._tick_clients[code] = {"tester": time.time()}


def test_ticks_poll_incremental():
    _seed("2330", [1, 2, 3])
    r = _client().get("/ticks?codes=2330&after=2330:1&client=tester").get_json()
    d = r["data"]["2330"]
    assert d["seq"] == 3
    assert [t[0] for t in d["ticks"]] == ["2026-11-12T09:00:02.000000",
                                          "2026-11-12T09:00:03.000000"]
    assert d["truncated"] is False
    assert d.get("reset") is None


def test_ticks_poll_truncated_when_ring_evicted():
    gw._tick_seq["2330"] = 10
    gw._tick_ring["2330"] = gw.deque([(9, "x", 1.0, 1, 1), (10, "y", 1.0, 1, 1)])
    gw._tick_clients["2330"] = {"tester": time.time()}
    d = _client().get("/ticks?codes=2330&after=2330:1&client=tester").get_json()["data"]["2330"]
    assert d["truncated"] is True


def test_ticks_poll_reset_when_cursor_ahead():
    _seed("2330", [1])
    d = _client().get("/ticks?codes=2330&after=2330:99&client=tester").get_json()["data"]["2330"]
    assert d["reset"] is True


def test_ticks_poll_reports_unsubscribed():
    gw._tick_seq["2330"] = 1
    d = _client().get("/ticks?codes=2330&after=2330:0").get_json()["data"]["2330"]
    assert d["subscribed"] is False


def test_ticks_poll_updates_client_timestamp():
    _seed("2330", [1])
    gw._tick_clients["2330"]["tester"] = 0.0
    _client().get("/ticks?codes=2330&after=2330:0&client=tester")
    assert gw._tick_clients["2330"]["tester"] > 0.0


# ── /ticks/subscribe ────────────────────────────────────────────

def _patch_subscribe(monkeypatch, api, sdk_ticks):
    calls = {"subscribe": 0, "unsubscribe": 0}

    def fake_run(fn, **kw):
        return fn(api)

    def fake_subscribe(contract, **kw):
        calls["subscribe"] += 1

    def fake_unsubscribe(contract, **kw):
        calls["unsubscribe"] += 1

    api.quote = SimpleNamespace(subscribe=fake_subscribe, unsubscribe=fake_unsubscribe)
    api.ticks = lambda **kw: sdk_ticks
    monkeypatch.setattr(gw, "_run", fake_run)
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: _fake_contract(c))
    monkeypatch.setattr(gw, "_conn", {"api": api})
    return calls


def _sdk_ticks(rows):
    """rows: [(ns_ts, close, volume, tick_type)]"""
    return SimpleNamespace(ts=[r[0] for r in rows], close=[r[1] for r in rows],
                           volume=[r[2] for r in rows], tick_type=[r[3] for r in rows])


def _ns(ts_iso):
    """把 wall-clock 字串轉成 SDK 的「本地時鐘當 epoch」ns 值。"""
    dt = datetime.fromisoformat(ts_iso)
    return int(dt.replace(tzinfo=gw.UTC).timestamp() * 1e9)


def test_subscribe_returns_metadata_and_resume_seq(monkeypatch):
    api = _fake_api()
    _patch_subscribe(monkeypatch, api, _sdk_ticks([]))
    r = _client().get("/ticks/subscribe?codes=2330&client=c1&backfill=0").get_json()
    d = r["data"]["2330"]
    assert d["subscribed"] is True
    assert d["name"] == "台積電"
    assert d["limit_up"] == 1100.0
    assert d["resume_seq"] == 0
    assert gw._tick_clients["2330"]["c1"] > 0


def test_subscribe_resume_seq_excludes_ticks_during_backfill(monkeypatch):
    """回補期間進來的即時 tick 不在 backfill 內，但序號 > resume_seq，輪詢會補上。"""
    api = _fake_api()
    cb = gw._make_on_tick(api)
    rows = [_ns("2026-11-12T09:00:01.000000"), 99.0, 1, 1], \
           [_ns("2026-11-12T09:00:02.000000"), 100.0, 2, 1]

    def fake_ticks(**kw):
        cb("TSE", _fake_tick(ts="2026-11-12T09:00:03.000000", close=101.0))
        return _sdk_ticks(rows)

    api.quote = SimpleNamespace(subscribe=lambda *a, **k: None)
    api.ticks = fake_ticks
    monkeypatch.setattr(gw, "_run", lambda fn, **kw: fn(api))
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: _fake_contract(c))
    monkeypatch.setattr(gw, "_conn", {"api": api})

    d = _client().get("/ticks/subscribe?codes=2330&client=c1&backfill=1").get_json()["data"]["2330"]
    # 回補只到 09:00:02；09:00:03 那筆留在 ring，序號 1 > resume_seq(0)
    assert [r[0] for r in d["backfill"]] == ["2026-11-12T09:00:01.000000",
                                             "2026-11-12T09:00:02.000000"]
    assert d["resume_seq"] == 0
    polled = _client().get("/ticks?codes=2330&after=2330:0&client=c1").get_json()["data"]["2330"]
    # tick 列格式：(ts, close, volume, tick_type)，與 backfill 相同
    assert [t[0] for t in polled["ticks"]] == ["2026-11-12T09:00:03.000000"]


def test_subscribe_since_ts_filters_rows(monkeypatch):
    api = _fake_api()
    rows = [(_ns("2026-11-12T09:00:01.000000"), 99.0, 1, 1),
            (_ns("2026-11-12T09:00:05.000000"), 100.0, 2, 1)]
    _patch_subscribe(monkeypatch, api, _sdk_ticks(rows))
    d = _client().get(
        "/ticks/subscribe?codes=2330&client=c1&backfill=1"
        "&since_ts=2026-11-12T09:00:03.000000").get_json()["data"]["2330"]
    assert [r[0] for r in d["backfill"]] == ["2026-11-12T09:00:05.000000"]


def test_run_timeout_does_not_mark_down_when_disabled(monkeypatch):
    """回補逾時不該重登：否則「逾時→重登→重訂閱」會變成迴圈（2026-11-13 事故）。"""
    api = _fake_api()
    monkeypatch.setattr(gw, "_conn", {"api": api})

    def boom(a):
        raise TimeoutError("slow kbars")

    with pytest.raises(TimeoutError):
        gw._run(boom, op="tick_backfill", mark_down_on_timeout=False)
    assert gw._last_session_down["count"] == 0
    assert gw._conn["api"] is api


def test_run_timeout_marks_down_by_default(monkeypatch):
    api = _fake_api()
    monkeypatch.setattr(gw, "_conn", {"api": api})
    monkeypatch.setattr(gw, "_relogin",
                        lambda deadline, prior: (_ for _ in ()).throw(
                            gw.GatewayReconnecting("test")))

    def boom(a):
        raise TimeoutError("slow")

    with pytest.raises(Exception):
        gw._run(boom, op="snapshot")
    assert gw._last_session_down["count"] == 1


def test_subscribe_backfill_failure_still_subscribed(monkeypatch):
    """回補失敗不可回 subscribed=false 又留下 SDK 訂閱（孤兒訂閱 sweep 掃不到）。"""
    api = _fake_api()
    calls = _patch_subscribe(monkeypatch, api, _sdk_ticks([]))

    def boom(**kw):
        raise TimeoutError("kbars timeout")

    api.ticks = boom
    d = _client().get("/ticks/subscribe?codes=2330&client=c1&backfill=1").get_json()["data"]["2330"]
    assert d["subscribed"] is True
    assert d["backfill"] == []
    assert calls["subscribe"] == 1
    assert gw._tick_clients["2330"]["c1"] > 0


def test_drop_tick_code_clears_registration():
    _seed("2330", [1, 2])
    gw._drop_tick_code("2330")
    assert "2330" not in gw._tick_clients
    assert "2330" not in gw._tick_ring
    assert "2330" not in gw._tick_meta
    assert gw._tick_seq["2330"] == 2      # 序號保持單調，不可歸零


def test_subscribe_refcount_two_clients(monkeypatch):
    api = _fake_api()
    calls = _patch_subscribe(monkeypatch, api, _sdk_ticks([]))
    c = _client()
    c.get("/ticks/subscribe?codes=2330&client=c1&backfill=0")
    c.get("/ticks/subscribe?codes=2330&client=c2&backfill=0")
    assert calls["subscribe"] == 1


def test_unsubscribe_only_at_zero_refcount(monkeypatch):
    api = _fake_api()
    calls = _patch_subscribe(monkeypatch, api, _sdk_ticks([]))
    c = _client()
    c.get("/ticks/subscribe?codes=2330&client=c1&backfill=0")
    c.get("/ticks/subscribe?codes=2330&client=c2&backfill=0")

    r = c.get("/ticks/unsubscribe?codes=2330&client=c1").get_json()
    assert r["data"]["2330"]["subscribed"] is True
    assert calls["unsubscribe"] == 0

    r = c.get("/ticks/unsubscribe?codes=2330&client=c2").get_json()
    assert r["data"]["2330"]["subscribed"] is False
    assert calls["unsubscribe"] == 1


# ── 歷史 tick 解析 ──────────────────────────────────────────────

def test_parse_sdk_ticks_filters_simtrade_and_formats_wallclock():
    ticks = SimpleNamespace(
        ts=[_ns("2026-11-12T09:00:01.500000"), _ns("2026-11-12T09:00:02.000000")],
        close=[100.0, 101.0], volume=[1, 2], tick_type=[1, 2],
        simtrade=[False, True])
    rows = gw._parse_sdk_ticks(ticks)
    assert rows == [("2026-11-12T09:00:01.500000", 100.0, 1, 1)]


# ── /snapshot 擴充欄位與遮蔽 ────────────────────────────────────

def test_snapshot_includes_extended_fields(monkeypatch):
    api = _fake_api()
    sn = SimpleNamespace(close=100.0, change_price=1.0, change_rate=1.01,
                         total_amount=5e9, total_volume=123, yesterday_volume=200,
                         buy_volume=30, sell_volume=25, high=101.0, low=99.0, open=99.5)
    api.snapshots = lambda contracts, timeout=None: [sn]
    monkeypatch.setattr(gw, "_run", lambda fn, **kw: fn(api))
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: _fake_contract(c))
    monkeypatch.setattr(gw, "_conn", {"api": api})

    d = _client().get("/snapshot?codes=2330").get_json()["data"]["2330"]
    assert d["total_volume"] == 123
    assert d["yesterday_volume"] == 200
    assert d["name"] == "台積電"
    assert d["limit_down"] == 900.0


def test_snapshot_error_redacts_jwt(monkeypatch):
    api = _fake_api()
    # 自造假 JWT（公開 repo 不可放真實 token）
    jwt = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0."
           "c2lnbmF0dXJlLW5vdC1yZWFs")

    def boom(fn, **kw):
        raise RuntimeError(f"payload: {{'token': '{jwt}'}}")

    monkeypatch.setattr(gw, "_run", boom)
    monkeypatch.setattr(gw, "_resolve_contract", lambda a, c: _fake_contract(c))
    monkeypatch.setattr(gw, "_conn", {"api": api})
    body = _client().get("/snapshot?codes=2330").get_json()
    assert "eyJ" not in body["error"]
    assert "<redacted-jwt>" in body["error"]


# ── /health ────────────────────────────────────────────────────

def test_health_includes_tick_fields():
    gw._tick_clients["2330"] = {"tester": time.time()}
    gw._tick_seq["2330"] = 2
    gw._tick_ring["2330"] = gw.deque([(1, "a", 1.0, 1, 1), (2, "b", 1.0, 1, 1)])
    gw._tick_meta["2330"] = {"last_tick_at": time.time()}
    h = _client().get("/health").get_json()
    assert h["boot_id"] == gw.BOOT_ID
    assert h["ticks"]["subscribed_codes"] == 1
    assert h["ticks"]["buffered_ticks"] == 2
    assert h["ticks"]["clients"] == ["tester"]
