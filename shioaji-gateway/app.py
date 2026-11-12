"""Shioaji 報價 Gateway (port 5455)

獨佔「唯一」一條 Shioaji 持久連線，供多個服務(market-analysis、ma_monitor…)
共用，避免各自 login 互踢 / 觸發登入限流。

端點：
  GET /health                              -> {status, logged_in}
  GET /snapshot?codes=IX0001,2330,2454     -> {ok, data:{code:{close,change_price,change_rate}}}
  GET /kbars?code=2330&days=30             -> {ok, closes:[...]}  (⚠️分K Close，非日K；日K請用 /daily_ohlcv)
  GET /intraday?code=IX0001[&date=2026-08-14] -> {ok, points:[{t:"09:01",price,high,low,volume},...]}  (指定日1分鐘走勢，預設當日)
  GET /scanner?type=pct|volume|amount&date=2026-08-28&count=200 -> {ok, items:[{code,name,close,change_price,pct,volume,amount},...]}  (行情排行，count上限200)
  GET /market_snapshot                     -> {ok, items:[{code,name,suffix,close,high,low,change_rate,total_volume},...]}  (全市場TSE+OTC即時快照)
"""
import heapq
import itertools
import os
import re
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta

from flask import Flask, jsonify, request

app = Flask(__name__)
SECRETS_DIR = "/Users/steven/CCProject/.secrets"

_conn = {"api": None}
_STOCK_CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")     # 個股 4 碼、ETF 5-6 碼，可加字母（00631L）


class LockTimeout(RuntimeError):
    """等鎖超過預算。不是連線問題，不可觸發重登。"""


class GatewayBusy(RuntimeError):
    """請求預算用盡 / 等鎖太久。"""


class GatewayReconnecting(RuntimeError):
    """連線正在重建中，請稍後重試。"""


class _PriorityLock:
    """等待佇列依 priority 排序（數字越小越優先），同 priority 內先到先服務。
    無法中斷已在執行中的呼叫，只影響「下一個輪到誰」。"""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._waiting = []
        self._counter = itertools.count()

    def acquire(self, priority=0, timeout=None):
        ticket = next(self._counter)
        entry = (priority, ticket)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            heapq.heappush(self._waiting, entry)
            try:
                while self._busy or self._waiting[0] != entry:
                    if deadline is None:
                        self._cv.wait()
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # 不可用 heappop：會拔掉佇列最前面的「別人」
                        self._waiting.remove(entry)
                        heapq.heapify(self._waiting)
                        self._cv.notify_all()
                        raise LockTimeout("priority lock wait timed out")
                    self._cv.wait(remaining)
                heapq.heappop(self._waiting)
                self._busy = True
            except BaseException:
                if entry in self._waiting:   # 已 pop 或已移除就不重複處理
                    self._waiting.remove(entry)
                    heapq.heapify(self._waiting)
                    self._cv.notify_all()
                raise

    def release(self):
        with self._cv:
            self._busy = False
            self._cv.notify_all()

    def stats(self):
        with self._cv:
            return {"busy": self._busy, "queue_depth": len(self._waiting)}


_lock = _PriorityLock()
_login_lock = threading.Lock()       # single-flight 登入；絕不與 _lock 嵌套
_state_cv = threading.Condition()    # leaf lock：只做純記憶體操作，不碰 SDK、不取其他鎖
_login_state = {
    "reconnecting": False, "failures": 0, "next_attempt_at": 0.0,
    "last_attempt": 0.0, "last_success": 0.0, "last_error": None, "generation": 0,
}
_last_ok = {"ts": 0.0}
_last_session_down = {"ts": 0.0, "count": 0}
_request_ctx = threading.local()     # 每個執行緒目前的請求 deadline

# 毫秒。SDK 預設 30000，死掉的 session 會讓人等滿 30 秒（2026-11-11 事故）
SNAPSHOT_TIMEOUT_MS = 4000
INTRADAY_TIMEOUT_MS = 4000
KBARS_TIMEOUT_MS = 8000
DAILY_TIMEOUT_MS = 15000
TICKS_TIMEOUT_MS = 12000
SCANNER_TIMEOUT_MS = 20000
MARKET_SNAPSHOT_BATCH_TIMEOUT_MS = 4000
MARKET_SNAPSHOT_TOTAL_S = 22.0
DEFAULT_BUDGET_S = 12.0
RECONNECT_WAIT_S = 2.5           # 重連期間其他請求最多等這麼久
LOGIN_BACKOFF_BASE_S = 2.0
LOGIN_BACKOFF_MAX_S = 30.0
SLOW_CALL_MS = 2000


def _sdk_timeout_ms(cap_ms):
    """回傳這次呼叫可用的 SDK timeout；不會超過整個請求的剩餘預算。"""
    deadline = getattr(_request_ctx, "deadline", None)
    if deadline is None:
        return cap_ms
    remaining = int((deadline - time.monotonic()) * 1000)
    # 下限 2 秒：500ms 會在負載高時把健康 session 誤判成逾時→連鎖重登
    return max(2000, min(cap_ms, remaining))


def _time_left():
    deadline = getattr(_request_ctx, "deadline", None)
    return None if deadline is None else deadline - time.monotonic()

_bidask_cache = {}        # code -> {"ts": float, "bids": [...], "asks": [...]}
_bidask_subscribed = {}   # code -> 最後被 /bidask 查詢的時間
BIDASK_IDLE_TIMEOUT = 90  # 超過這麼久沒人查詢就自動退訂
BIDASK_SWEEP_INTERVAL = 60


def _is_candidate_code(code):
    """隔日沖候選代號：4碼個股、5碼數字 ETF、00xxxX 字尾 ETF（含正/反 ETF）；排除 6 碼數字權證"""
    return ((len(code) in (4, 5) and code.isdigit())
            or bool(re.fullmatch(r"00\d{3}[A-Z]", code)))


def _read_secret(filename):
    with open(os.path.join(SECRETS_DIR, filename), encoding="utf-8") as f:
        return f.read().strip()


def _make_on_bidask(api):
    """每個 session 專屬的五檔回呼。快取帶 api_id，重登後舊 session 的殘留資料不會被當成即時價。"""
    api_id = id(api)

    def _on_bidask(exchange, bidask):
        try:
            _bidask_cache[bidask.code] = {
                "ts": time.time(),
                "api_id": api_id,
                "bids": [
                    {"price": float(p), "volume": int(v)}
                    for p, v in zip(bidask.bid_price, bidask.bid_volume)
                ],
                "asks": [
                    {"price": float(p), "volume": int(v)}
                    for p, v in zip(bidask.ask_price, bidask.ask_volume)
                ],
            }
        except Exception:
            pass

    return _on_bidask


def _handle_session_down(dead_api_id):
    """SDK 的 session-down 回呼（在 Rust 執行緒上跑）。

    只碰 _state_cv：取 _lock 會跟正在等 SDK 回覆的執行緒互鎖，直接死鎖整個 gateway。
    id() 比對避免舊 session 的延遲回呼把剛建立的新 session 清掉。
    """
    with _state_cv:
        current = _conn["api"]
        if current is None or id(current) != dead_api_id:
            return
        _conn["api"] = None
        _last_session_down["ts"] = time.time()
        _last_session_down["count"] += 1
        _state_cv.notify_all()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] session down; api cleared", flush=True)


def _login():
    import shioaji as sj
    api_key = os.environ.get("SHIOAJI_API_KEY") or _read_secret("shioaji_api.txt")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY") or _read_secret("shioaji_secret.txt")
    api = sj.Shioaji(simulation=False)
    api.login(api_key=api_key, secret_key=secret_key)
    api.quote.set_on_bidask_stk_v1_callback(_make_on_bidask(api))
    register = getattr(api, "set_session_down_callback", None) or getattr(api, "on_session_down", None)
    if register is not None:
        api_id = id(api)
        register(lambda: _handle_session_down(api_id))
    return api


def _is_conn_error(e):
    """連線/認證類錯誤才重登，業務錯誤（KeyError 查無契約、ValueError 參數錯）重登也救不了，
    反而讓所有並行客戶端白白斷線重連一次（每次約 2s，期間全體排隊）。
    2026-10-28 實測：連打 3 個不存在的代碼 → 3 次重登。

    判定改採「排除已知業務例外」而非白名單 —— 2026-10-29 事故：session 未建立時 Rust 層
    拋的是 'contracts: transient transport failure ... SubCode(SessionNotEstablished)'，
    不在原本的白名單（AuthError/ConnectionError/ResponseRecvError/ServerError/TimeoutError）
    內 → 每個請求 0.014 秒直接回錯且永不重登，gateway 從 01:00 卡到 09:20 全服務取價失敗。
    白名單天生會漏掉未知例外類型；改成未知一律當連線問題（fail-safe），只有確定重登無用的
    才原樣往上拋。"""
    import shioaji as sj
    return not isinstance(e, (LookupError, ValueError, TypeError, AttributeError,
                              LockTimeout, GatewayBusy, GatewayReconnecting,
                              sj.ValidationError, sj.BadRequestError))


def _note_success():
    with _state_cv:
        _last_ok["ts"] = time.time()


def _mark_down(api, exc):
    with _state_cv:
        if _conn["api"] is api:
            _conn["api"] = None
            _last_session_down["ts"] = time.time()
            _last_session_down["count"] += 1
        _login_state["last_error"] = f"{type(exc).__name__}: {exc}"
        _state_cv.notify_all()


def _ensure_api(deadline):
    """拿到一個可用的 api；需要重登時只有一個執行緒去登，其他人最多等 RECONNECT_WAIT_S。

    等待要有「總量」上限：每次醒來再等一輪的話，會一路等到整個請求預算用完
    （2026-11-11 實測：重連期間請求仍卡到 12 秒）。"""
    reconnect_deadline = min(deadline, time.monotonic() + RECONNECT_WAIT_S)
    while True:
        with _state_cv:
            api = _conn["api"]
            if api is not None:
                return api
            if _login_state["reconnecting"]:
                wait = reconnect_deadline - time.monotonic()
                if wait > 0:
                    _state_cv.wait(wait)
                    continue
                raise GatewayReconnecting("gateway reconnecting")
            if time.monotonic() < _login_state["next_attempt_at"]:
                raise GatewayReconnecting(_login_state["last_error"] or "login backoff")
        # 已離開 _state_cv 才去取 _login_lock（鎖順序：_state_cv 是 leaf）
        return _relogin(deadline, None)


def _quiet_logout(api):
    """舊 session 盡量登出，任何失敗都吞掉（背景執行緒用）。"""
    try:
        api.logout()
    except Exception:
        pass


def _relogin(deadline, prior_api):
    """single-flight 重登。全程不持 _lock，所以其他請求不會陪等登入。"""
    remaining = min(max(0.0, deadline - time.monotonic()), RECONNECT_WAIT_S)
    if not _login_lock.acquire(timeout=remaining):
        raise GatewayReconnecting("login already in progress")
    try:
        with _state_cv:
            api = _conn["api"]
            if api is not None and api is not prior_api:
                return api          # 別的執行緒已經補好了
            was_marked_down = api is None
            _login_state["reconnecting"] = True
            _login_state["last_attempt"] = time.time()
        old = prior_api
        try:
            if old is not None:
                try:
                    old.clear_session_down_callback()
                except Exception:
                    pass
                # 必須「先登出舊的、再登入新的」：反過來的話 SDK 會在登入後才釋放
                # 共用的 token slot（log: Successfully released token slot 1），
                # 把剛拿到的新 token 一起作廢，變成每 5 分鐘一次的重登迴圈。
                _quiet_logout(old)
            new_api = _login()
        except BaseException as e:
            with _state_cv:
                _login_state["reconnecting"] = False
                _login_state["failures"] += 1
                backoff = min(LOGIN_BACKOFF_MAX_S,
                              LOGIN_BACKOFF_BASE_S * (2 ** (_login_state["failures"] - 1)))
                _login_state["next_attempt_at"] = time.monotonic() + backoff
                _login_state["last_error"] = f"{type(e).__name__}: {e}"
                _conn["api"] = None
                _state_cv.notify_all()
            raise
        with _state_cv:
            _conn["api"] = new_api
            _login_state["reconnecting"] = False
            _login_state["failures"] = 0
            _login_state["next_attempt_at"] = 0.0
            _login_state["last_success"] = time.time()
            _login_state["last_error"] = None
            _login_state["generation"] += 1
            generation = _login_state["generation"]
            _state_cv.notify_all()
    finally:
        _login_lock.release()       # 先放掉重連鎖，_run 之後才會重取 _lock
    _spawn_bidask_resubscribe(new_api, generation)
    return new_api


def _run(fn, priority=0, op="?", budget_s=DEFAULT_BUDGET_S, retry=True):
    """用持久連線執行 fn(api)。

    重登一律在 _lock 之外進行（2026-11-11 事故：重登期間所有請求被鎖住 30-60 秒）。
    整個請求有預算上限，等鎖太久或預算用盡就快速失敗。
    """
    deadline = time.monotonic() + budget_s
    _request_ctx.deadline = deadline
    started = time.monotonic()
    lock_wait_ms = 0
    outcome = "ok"
    try:
        api = _ensure_api(deadline)
        for attempt in (0, 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "budget"
                raise GatewayBusy("request budget exhausted")
            t = time.monotonic()
            try:
                _lock.acquire(priority, timeout=remaining)
            except LockTimeout:
                outcome = "busy"
                raise GatewayBusy("gateway busy: queue wait exceeded") from None
            lock_wait_ms += int((time.monotonic() - t) * 1000)
            try:
                api = _conn["api"]
                if api is None:
                    if attempt == 1:
                        outcome = "reconnecting"
                        raise GatewayReconnecting("gateway reconnecting")
                    # 排隊期間 session 掉了：交給迴圈尾端重登後再試一次
                else:
                    try:
                        result = fn(api)
                        _note_success()
                        outcome = "ok" if attempt == 0 else "retry_ok"
                        return result
                    except Exception as e:
                        if not _is_conn_error(e):
                            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 業務錯誤不重登 "
                                  f"{type(e).__name__}: {e}", flush=True)
                            outcome = "business_error"
                            raise
                        _mark_down(api, e)
                        outcome = "retry_failed"
                        if attempt == 1 or not retry:
                            raise
            finally:
                _lock.release()     # 一定要先放資料鎖，再去重登
            api = _relogin(deadline, api)
            if deadline - time.monotonic() <= 0.5:
                outcome = "reconnecting"
                raise GatewayReconnecting("reconnected; retry budget exhausted")
    finally:
        _request_ctx.deadline = None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > SLOW_CALL_MS or outcome in ("retry_ok", "reconnecting", "busy", "budget"):
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] call op={op} elapsed_ms={elapsed_ms} "
                  f"lock_wait_ms={lock_wait_ms} outcome={outcome} "
                  f"gen={_login_state['generation']}", flush=True)


def _resolve_contract(api, code):
    if code.startswith("IX"):          # 指數，如 IX0001（加權指數）
        return api.Contracts.Indexs.TSE[code]
    # 個股：先找上市(TSE)、再上櫃(OTC)、最後全域 bracket
    return (api.Contracts.Stocks.TSE.get(code)
            or api.Contracts.Stocks.OTC.get(code)
            or api.Contracts.Stocks[code])


@app.get("/health")
def health():
    """不動 _lock、不碰 SDK，永遠毫秒級回應（事故期間也要能當燈號）。"""
    with _state_cv:
        api = _conn["api"]
        reconnecting = _login_state["reconnecting"]
        last_ok = _last_ok["ts"]
        last_error = _login_state["last_error"]
        generation = _login_state["generation"]
        down_ts = _last_session_down["ts"]
        down_count = _last_session_down["count"]
    now = time.time()
    if api is not None:
        state = "ok"
    elif reconnecting or (down_ts and now - down_ts < 30):
        state = "reconnecting"
    else:
        state = "degraded"
    stats = _lock.stats()
    return jsonify({
        "status": "ok",
        "logged_in": api is not None,
        "state": state,
        "reconnecting": reconnecting,
        "last_ok_age_s": round(max(0.0, now - last_ok), 1) if last_ok else None,
        "session_down_age_s": round(max(0.0, now - down_ts), 1) if down_ts else None,
        "session_down_count": down_count,
        "last_error": last_error,
        "generation": generation,
        "queue_depth": stats["queue_depth"],
        "lock_busy": stats["busy"],
    })


@app.get("/snapshot")
def snapshot():
    codes = [c for c in (request.args.get("codes", "").split(",")) if c]
    if not codes:
        return jsonify({"ok": False, "error": "no codes"}), 400
    try:
        def work(api):
            contracts = [_resolve_contract(api, c) for c in codes]
            snaps = api.snapshots(contracts, timeout=_sdk_timeout_ms(SNAPSHOT_TIMEOUT_MS))
            out = {}
            for c, sn in zip(codes, snaps):
                out[c] = {
                    "close": float(sn.close),
                    "change_price": float(sn.change_price),
                    "change_rate": float(sn.change_rate) if sn.change_rate is not None else None,
                    "total_amount": float(sn.total_amount) if sn.total_amount is not None else None,
                }
            return out
        return jsonify({"ok": True, "data": _run(work, op="snapshot", budget_s=8)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/stock_search")
def stock_search():
    """模糊搜尋代號或中文名稱，回傳最多 limit 筆 [{code, name}]。

    原本只收 4 碼個股，導致 006208（富邦台50）這類 6 碼 ETF 永遠查不到；
    改成 4-6 碼數字（可加一個字母），並支援 limit。
    """
    q = request.args.get("q", "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", "10")), 50))
    except ValueError:
        limit = 10
    if not q:
        return jsonify({"ok": True, "results": []})
    try:
        def work(api):
            results = []
            for market in (api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC):
                for contract in market:
                    code = getattr(contract, "code", "")
                    name = getattr(contract, "name", "")
                    if not _STOCK_CODE_RE.match(code):
                        continue
                    if q in code or q in name:
                        results.append({"code": code, "name": name})
                    if len(results) >= limit:
                        return results
            return results
        return jsonify({"ok": True, "results": _run(work, op="stock_search", budget_s=3)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/kbars")
def kbars():
    """回傳 Shioaji **分K** 的 Close 序列（不是日K）。需要日K請用 /daily_ohlcv。"""
    code = request.args.get("code", "")
    days = int(request.args.get("days", "30"))
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            end = date.today()
            start = end - timedelta(days=max(days * 2, 60))
            kb = api.kbars(
                contract=contract,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                timeout=_sdk_timeout_ms(KBARS_TIMEOUT_MS),
            )
            return [float(c) for c in kb.Close if c]
        return jsonify({"ok": True, "closes": _run(work, op="kbars", budget_s=12)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/daily_ohlcv")
def daily_ohlcv():
    """日K聚合（由分K依日期分組彙整），供需要開高低收量的策略計算使用。
    GET /daily_ohlcv?code=2330&days=90 -> {ok, bars:[{date,open,high,low,close,volume}, ...]}（舊到新）"""
    code = request.args.get("code", "")
    days = int(request.args.get("days", "90"))
    priority = -1 if request.args.get("priority") == "high" else 0
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            end = date.today()
            start = end - timedelta(days=max(days * 2, 60))
            kb = api.kbars(
                contract=contract,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                timeout=_sdk_timeout_ms(DAILY_TIMEOUT_MS),
            )
            daily = {}
            for ts, o, h, l, c, v in zip(kb.ts, kb.Open, kb.High, kb.Low, kb.Close, kb.Volume):
                if c is None:
                    continue
                d = datetime.fromtimestamp(ts / 1e9, tz=UTC).date().isoformat()
                bar = daily.get(d)
                if bar is None:
                    daily[d] = {"date": d, "open": float(o), "high": float(h),
                                "low": float(l), "close": float(c), "volume": int(v)}
                else:
                    bar["high"] = max(bar["high"], float(h))
                    bar["low"] = min(bar["low"], float(l))
                    bar["close"] = float(c)
                    bar["volume"] += int(v)
            bars = [daily[d] for d in sorted(daily.keys())]
            return bars[-days:]
        bars = _run(work, priority=priority, op="daily_ohlcv", budget_s=20)
        bars = _append_today_via_yfinance(code, bars)
        return jsonify({"ok": True, "bars": bars})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _append_today_via_yfinance(code, bars):
    """Shioaji 配額耗盡或今日資料尚未入庫時，缺當天日K就改用 yfinance 補一根延遲收盤（fail-open，補不到就原樣回傳）。
    yfinance volume 單位是股，除以1000換算成張以跟 Shioaji 資料一致。"""
    today_str = date.today().isoformat()
    if bars and bars[-1]["date"] == today_str:
        return bars
    try:
        import yfinance as yf
        for suffix in (".TW", ".TWO"):
            try:
                fi = yf.Ticker(f"{code}{suffix}").fast_info
                price = fi.last_price
                if not price:
                    continue
                return bars + [{
                    "date": today_str,
                    "open": float(fi.open) if fi.open else float(price),
                    "high": float(fi.day_high) if fi.day_high else float(price),
                    "low": float(fi.day_low) if fi.day_low else float(price),
                    "close": float(price),
                    "volume": int(fi.last_volume / 1000) if fi.last_volume else 0,
                }]
            except Exception:
                continue
    except Exception:
        pass
    return bars


@app.get("/intraday")
def intraday():
    code = request.args.get("code", "")
    target_date = request.args.get("date") or date.today().strftime("%Y-%m-%d")
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            kb = api.kbars(contract=contract, start=target_date, end=target_date,
                           timeout=_sdk_timeout_ms(INTRADAY_TIMEOUT_MS))
            points = []
            for ts, high, low, close, volume in zip(kb.ts, kb.High, kb.Low, kb.Close, kb.Volume):
                if close is None:
                    continue
                # kb.ts 是「台灣本地時間」數值但以 epoch 秒編碼（非真正 UTC），
                # 用 utcfromtimestamp 直接取數值對應的時鐘時間，避免 fromtimestamp 多轉一次時區造成 +8 小時位移
                t = datetime.fromtimestamp(ts / 1e9, tz=UTC)
                points.append({
                    "t": t.strftime("%H:%M"),
                    "price": float(close),
                    "high": float(high) if high is not None else None,
                    "low": float(low) if low is not None else None,
                    "volume": int(volume),
                })
            return points
        return jsonify({"ok": True, "points": _run(work, op="intraday", budget_s=12)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/big_orders")
def big_orders():
    """指定日逐筆大單內外盤淨額（截至 end 時間，預設當日）。
    大單 = 單筆 100萬(含)~400萬(未滿)，特大單 = 單筆 400萬(含)以上；tick_type 1=外盤、2=內盤。
    GET /big_orders?code=2330&end=12:58&date=2026-10-16 -> {ok, big_net, super_net, combined_net, big_count, super_count, tick_count}"""
    code = request.args.get("code", "").strip()
    end = request.args.get("end", "12:58")
    target_date = request.args.get("date") or date.today().strftime("%Y-%m-%d")
    if not code:
        return jsonify({"ok": False, "error": "code 必填"})
    try:
        def work(api):
            contract = _resolve_contract(api, code)
            ticks = api.ticks(contract=contract, date=target_date,
                              timeout=_sdk_timeout_ms(TICKS_TIMEOUT_MS))
            big_net = super_net = 0
            big_count = super_count = 0
            cutoff = f"{end}:59"
            for ts, close, volume, tick_type in zip(ticks.ts, ticks.close, ticks.volume, ticks.tick_type):
                hhmmss = datetime.fromtimestamp(ts / 1e9, tz=UTC).strftime("%H:%M:%S")
                if hhmmss > cutoff:
                    continue
                amount = close * volume * 1000
                side = 1 if tick_type == 1 else -1 if tick_type == 2 else 0
                if amount >= 4_000_000:
                    super_net += side * amount
                    super_count += 1
                elif amount >= 1_000_000:
                    big_net += side * amount
                    big_count += 1
            return {
                "tick_count": len(ticks.ts),
                "big_net": big_net,
                "super_net": super_net,
                "combined_net": big_net + super_net,
                "big_count": big_count,
                "super_count": super_count,
            }
        return jsonify({"ok": True, **_run(work, op="big_orders", budget_s=18)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


SCANNER_KINDS = {"pct": "ChangePercentRank", "volume": "VolumeRank", "amount": "AmountRank"}


def _scanner_kind(value: str) -> str:
    """查詢字串 → Shioaji 列舉名稱；未知值丟 ValueError（不靜默退回漲跌幅）。"""
    key = (value or "pct").strip().lower()
    if key not in SCANNER_KINDS:
        raise ValueError(f"未知排行類型: {value}")
    return SCANNER_KINDS[key]


@app.get("/scanner")
def scanner():
    """行情排行掃描（跨全市場，非單檔查詢）。count 上限 200（Shioaji 限制）。
    GET /scanner?type=pct|volume|amount&date=2026-08-28&count=200&ascending=false
    -> {ok, items:[{code,name,close,change_price,pct,volume,amount},...]}"""
    import shioaji as sj
    target_date = request.args.get("date") or date.today().strftime("%Y-%m-%d")
    try:
        count = max(1, min(int(request.args.get("count", "200")), 200))
        scanner_type = getattr(sj.constant.ScannerType, _scanner_kind(request.args.get("type", "")))
    except (ValueError, AttributeError) as e:
        return jsonify({"ok": False, "error": f"參數錯誤: {e}"})
    ascending = request.args.get("ascending", "false").lower() == "true"
    try:
        def work(api):
            items = api.scanners(
                scanner_type=scanner_type,
                date=target_date, ascending=ascending, count=count,
                timeout=_sdk_timeout_ms(SCANNER_TIMEOUT_MS),
            )
            out = []
            for it in items:
                prev_close = it.close - it.change_price
                pct = (it.change_price / prev_close * 100) if prev_close else None
                out.append({"code": it.code, "name": it.name.strip(), "close": float(it.close),
                            "change_price": float(it.change_price), "pct": pct,
                            # volume/amount 是當筆量，排行要看日累計
                            "volume": int(it.total_volume or 0), "amount": int(it.total_amount or 0)})
            return out
        return jsonify({"ok": True, "items": _run(work, op="scanner", budget_s=25, retry=False)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/market_snapshot")
def market_snapshot():
    """全市場即時快照（上市 TSE + 上櫃 OTC 4 碼個股），供全市場初篩與量能排名使用。
    priority 給 1（低於預設 0），讓 ma_monitor 等短查詢優先取用連線。
    GET /market_snapshot -> {ok, items:[{code,name,suffix,close,high,low,change_rate,total_volume}]}"""
    def work(api):
        items = []
        partial = False
        total_deadline = time.monotonic() + MARKET_SNAPSHOT_TOTAL_S
        consecutive_failures = 0
        for market, suffix in (("TSE", ".TW"), ("OTC", ".TWO")):
            if partial:
                break
            try:
                board = getattr(api.Contracts.Stocks, market)
                contracts = [c for c in board if _is_candidate_code(getattr(c, "code", ""))]
            except Exception:
                continue
            cmap = {c.code: c for c in contracts}
            for i in range(0, len(contracts), 200):
                if time.monotonic() > total_deadline:
                    partial = True
                    break
                try:
                    snaps = api.snapshots(
                        contracts[i:i + 200],
                        timeout=_sdk_timeout_ms(MARKET_SNAPSHOT_BATCH_TIMEOUT_MS))
                    consecutive_failures = 0
                except Exception:
                    # 單批失敗先跳過；連續 3 批就停手。已抓到的資料仍回傳並標 partial，
                    # 不要把部分成功變成整個端點失敗（呼叫端有 fallback 但會退到昨日資料）
                    consecutive_failures += 1
                    if _conn["api"] is None:
                        raise
                    if consecutive_failures >= 3:
                        partial = True
                        break
                    continue
                for s in snaps:
                    try:  # 單筆髒資料（停牌/None）只跳過，勿讓整批或整個市場失敗
                        name = getattr(cmap.get(s.code), "name", s.code)
                        items.append({
                            "code": s.code,
                            "name": (name or s.code).strip(),
                            "suffix": suffix,
                            "close": float(s.close) if s.close else None,
                            "high": float(s.high) if s.high is not None else None,
                            "low": float(s.low) if s.low is not None else None,
                            "change_rate": float(s.change_rate) if s.change_rate is not None else None,
                            "total_volume": int(s.total_volume) if s.total_volume else 0,
                        })
                    except Exception:
                        continue
        if not items:
            # 不可把空清單當成功回傳：呼叫端會誤判成「市場沒資料」而非「抓取失敗」
            raise RuntimeError('全市場快照為空（session 失效或非交易日）')
        return items, partial
    try:
        items, partial = _run(work, priority=1, op="market_snapshot",
                              budget_s=25, retry=False)
        payload = {"ok": True, "items": items}
        if partial:
            payload["partial"] = True
        return jsonify(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/bidask")
def bidask():
    """五檔即時報價。按需訂閱：第一次查詢某代號會觸發訂閱，資料透過 callback 非同步送達，
    所以第一次查詢通常還沒有資料，前端下一次輪詢（1.5秒後）就會有了。"""
    code = request.args.get("code", "")
    if not code:
        return jsonify({"ok": False, "error": "no code"}), 400

    is_new = code not in _bidask_subscribed
    _bidask_subscribed[code] = time.time()

    if is_new:
        try:
            def work(api):
                import shioaji as sj
                contract = _resolve_contract(api, code)
                api.quote.subscribe(
                    contract,
                    quote_type=sj.constant.QuoteType.BidAsk,
                    version=sj.constant.QuoteVersion.v1,
                )
            _run(work, op="bidask_sub", budget_s=4)
        except Exception as e:
            _bidask_subscribed.pop(code, None)
            return jsonify({"ok": False, "error": f"訂閱失敗: {e}"})
        return jsonify({"ok": False, "error": "訂閱中，請稍後重新查詢"})

    current = _conn["api"]
    cached = _bidask_cache.get(code)
    # 舊 session 留下的快取不能當即時價（重登後訂閱尚未建立）
    if current is None or not cached or cached.get("api_id") != id(current):
        return jsonify({"ok": False, "error": "訂閱中，請稍後重新查詢"})
    payload = {k: v for k, v in cached.items() if k != "api_id"}
    return jsonify({"ok": True, "code": code, **payload})


def _spawn_bidask_resubscribe(api, generation):
    """重登後把仍在使用的五檔訂閱補回來；不經 _run，避免失敗時遞迴重登。"""
    codes = list(_bidask_subscribed.keys())
    if not codes:
        return

    def run():
        import shioaji as sj
        for code in codes:
            with _state_cv:
                if _conn["api"] is not api or _login_state["generation"] != generation:
                    return
            try:
                # 不經 _run（避免失敗時遞迴重登），但仍要共用資料鎖：
                # SDK 是單一連線，不能跟 snapshots/kbars 併發
                _lock.acquire(timeout=5)
            except LockTimeout:
                continue
            try:
                with _state_cv:
                    if _conn["api"] is not api or _login_state["generation"] != generation:
                        return
                if code not in _bidask_subscribed:
                    continue      # 期間已被 sweep 退訂，不要再訂回來
                contract = _resolve_contract(api, code)
                api.quote.subscribe(
                    contract,
                    quote_type=sj.constant.QuoteType.BidAsk,
                    version=sj.constant.QuoteVersion.v1,
                )
            except Exception as e:
                print(f"[bidask] 重訂閱 {code} 失敗（忽略）: {e}", flush=True)
            finally:
                _lock.release()

    threading.Thread(target=run, daemon=True, name="bidask-resub").start()


def _bidask_sweep_worker():
    """背景執行緒：定期退訂太久沒人查詢的五檔訂閱，避免佔用 Shioaji 連線資源。"""
    import shioaji as sj
    while True:
        time.sleep(BIDASK_SWEEP_INTERVAL)
        now = time.time()
        stale = [c for c, last in _bidask_subscribed.items() if now - last > BIDASK_IDLE_TIMEOUT]
        for code in stale:
            # session 不在時不要為了退訂去觸發一次重登，直接丟掉紀錄即可
            if _conn["api"] is not None:
                try:
                    def work(api, code=code):
                        contract = _resolve_contract(api, code)
                        api.quote.unsubscribe(
                            contract,
                            quote_type=sj.constant.QuoteType.BidAsk,
                            version=sj.constant.QuoteVersion.v1,
                        )
                    _run(work, op="bidask_unsub", budget_s=4)
                    print(f"[bidask] 閒置退訂 {code}")
                except Exception as e:
                    print(f"[bidask] 退訂 {code} 失敗（忽略）: {e}")
            _bidask_subscribed.pop(code, None)
            _bidask_cache.pop(code, None)


# yfinance 快取連線清理（共用模組；為何需要 threads=False 的實測數據見 lib/yf_guard.py）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from yf_guard import register  # noqa: I001


register(app)


if __name__ == "__main__":
    threading.Thread(target=_bidask_sweep_worker, daemon=True, name="bidask-sweep").start()
    app.run(host="127.0.0.1", port=5455)
