"""Shioaji 報價 Gateway (port 5455)

獨佔「唯一」一條 Shioaji 持久連線，供多個服務(market-analysis、ma_monitor…)
共用，避免各自 login 互踢 / 觸發登入限流。

端點：
  GET /health                              -> {status, logged_in}
  GET /snapshot?codes=IX0001,2330,2454     -> {ok, data:{code:{close,change_price,change_rate}}}
  GET /kbars?code=2330&days=30             -> {ok, closes:[...]}  (與 api.kbars(...).Close 相同)
  GET /intraday?code=IX0001[&date=2026-08-14] -> {ok, points:[{t:"09:01",price:23458.1},...]}  (指定日1分鐘走勢，預設當日)
  GET /scanner?date=2026-08-28&count=2000     -> {ok, items:[{code,name,close,change_price,pct},...]}  (全市場漲跌幅排行)
"""
import heapq
import itertools
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)
SECRETS_DIR = "/Users/steven/CCProject/.secrets"

_conn = {"api": None}


class _PriorityLock:
    """等待佇列依 priority 排序（數字越小越優先），同 priority 內先到先服務。
    無法中斷已在執行中的呼叫，只影響「下一個輪到誰」。"""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._waiting = []
        self._counter = itertools.count()

    def acquire(self, priority=0):
        ticket = next(self._counter)
        entry = (priority, ticket)
        with self._cv:
            heapq.heappush(self._waiting, entry)
            while self._busy or self._waiting[0] != entry:
                self._cv.wait()
            heapq.heappop(self._waiting)
            self._busy = True

    def release(self):
        with self._cv:
            self._busy = False
            self._cv.notify_all()


_lock = _PriorityLock()

_bidask_cache = {}        # code -> {"ts": float, "bids": [...], "asks": [...]}
_bidask_subscribed = {}   # code -> 最後被 /bidask 查詢的時間
BIDASK_IDLE_TIMEOUT = 90  # 超過這麼久沒人查詢就自動退訂
BIDASK_SWEEP_INTERVAL = 60


def _read_secret(filename):
    with open(os.path.join(SECRETS_DIR, filename), "r", encoding="utf-8") as f:
        return f.read().strip()


def _on_bidask(exchange, bidask):
    """Shioaji 五檔即時回呼，純寫入快取，不做其他事。"""
    try:
        _bidask_cache[bidask.code] = {
            "ts": time.time(),
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


def _login():
    import shioaji as sj
    api_key = os.environ.get("SHIOAJI_API_KEY") or _read_secret("shioaji_api.txt")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY") or _read_secret("shioaji_secret.txt")
    api = sj.Shioaji(simulation=False)
    api.login(api_key=api_key, secret_key=secret_key)
    api.quote.set_on_bidask_stk_v1_callback(_on_bidask)
    return api


def _run(fn, priority=0):
    """在鎖內用持久連線執行 fn(api)；連線失效時自動重登一次再試。
    priority 數字越小越優先（預設0），供大量背景批次請求插隊用。"""
    _lock.acquire(priority)
    try:
        try:
            if _conn["api"] is None:
                _conn["api"] = _login()
            return fn(_conn["api"])
        except Exception:
            try:
                if _conn["api"] is not None:
                    try:
                        _conn["api"].logout()
                    except Exception:
                        pass
                _conn["api"] = _login()
                return fn(_conn["api"])
            except Exception:
                _conn["api"] = None
                raise
    finally:
        _lock.release()


def _resolve_contract(api, code):
    if code.startswith("IX"):          # 指數，如 IX0001（加權指數）
        return api.Contracts.Indexs.TSE[code]
    # 個股：先找上市(TSE)、再上櫃(OTC)、最後全域 bracket
    return (api.Contracts.Stocks.TSE.get(code)
            or api.Contracts.Stocks.OTC.get(code)
            or api.Contracts.Stocks[code])


@app.get("/health")
def health():
    return jsonify({"status": "ok", "logged_in": _conn["api"] is not None})


@app.get("/snapshot")
def snapshot():
    codes = [c for c in (request.args.get("codes", "").split(",")) if c]
    if not codes:
        return jsonify({"ok": False, "error": "no codes"}), 400
    try:
        def work(api):
            contracts = [_resolve_contract(api, c) for c in codes]
            snaps = api.snapshots(contracts)
            out = {}
            for c, sn in zip(codes, snaps):
                out[c] = {
                    "close": float(sn.close),
                    "change_price": float(sn.change_price),
                    "change_rate": float(sn.change_rate) if sn.change_rate is not None else None,
                    "total_amount": float(sn.total_amount) if sn.total_amount is not None else None,
                }
            return out
        return jsonify({"ok": True, "data": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/stock_search")
def stock_search():
    """模糊搜尋股票代碼或中文名稱，回傳最多 10 筆 [{code, name}]。"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": True, "results": []})
    try:
        def work(api):
            results = []
            for market in (api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC):
                for contract in market:
                    code = getattr(contract, "code", "")
                    name = getattr(contract, "name", "")
                    if not (len(code) == 4 and code.isdigit()):
                        continue
                    if q in code or q in name:
                        results.append({"code": code, "name": name})
                    if len(results) >= 10:
                        return results
            return results
        return jsonify({"ok": True, "results": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/kbars")
def kbars():
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
            )
            return [float(c) for c in kb.Close if c]
        return jsonify({"ok": True, "closes": _run(work)})
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
            )
            daily = {}
            for ts, o, h, l, c, v in zip(kb.ts, kb.Open, kb.High, kb.Low, kb.Close, kb.Volume):
                if c is None:
                    continue
                d = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).date().isoformat()
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
        bars = _run(work, priority=priority)
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
            kb = api.kbars(contract=contract, start=target_date, end=target_date)
            points = []
            for ts, close, volume in zip(kb.ts, kb.Close, kb.Volume):
                if close is None:
                    continue
                # kb.ts 是「台灣本地時間」數值但以 epoch 秒編碼（非真正 UTC），
                # 用 utcfromtimestamp 直接取數值對應的時鐘時間，避免 fromtimestamp 多轉一次時區造成 +8 小時位移
                t = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                points.append({"t": t.strftime("%H:%M"), "price": float(close), "volume": int(volume)})
            return points
        return jsonify({"ok": True, "points": _run(work)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.get("/scanner")
def scanner():
    """漲跌幅排行掃描（跨全市場，非單檔查詢）。
    GET /scanner?date=2026-08-28&count=2000&ascending=false -> {ok, items:[{code,name,close,change_price,pct},...]}"""
    import shioaji as sj
    target_date = request.args.get("date") or date.today().strftime("%Y-%m-%d")
    count = int(request.args.get("count", "500"))
    ascending = request.args.get("ascending", "false").lower() == "true"
    try:
        def work(api):
            items = api.scanners(
                scanner_type=sj.constant.ScannerType.ChangePercentRank,
                date=target_date, ascending=ascending, count=count,
            )
            out = []
            for it in items:
                prev_close = it.close - it.change_price
                pct = (it.change_price / prev_close * 100) if prev_close else None
                out.append({"code": it.code, "name": it.name, "close": float(it.close),
                            "change_price": float(it.change_price), "pct": pct})
            return out
        return jsonify({"ok": True, "items": _run(work)})
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
            _run(work)
        except Exception as e:
            _bidask_subscribed.pop(code, None)
            return jsonify({"ok": False, "error": f"訂閱失敗: {e}"})
        return jsonify({"ok": False, "error": "訂閱中，請稍後重新查詢"})

    cached = _bidask_cache.get(code)
    if not cached:
        return jsonify({"ok": False, "error": "訂閱中，請稍後重新查詢"})
    return jsonify({"ok": True, "code": code, **cached})


def _bidask_sweep_worker():
    """背景執行緒：定期退訂太久沒人查詢的五檔訂閱，避免佔用 Shioaji 連線資源。"""
    import shioaji as sj
    while True:
        time.sleep(BIDASK_SWEEP_INTERVAL)
        now = time.time()
        stale = [c for c, last in _bidask_subscribed.items() if now - last > BIDASK_IDLE_TIMEOUT]
        for code in stale:
            try:
                def work(api, code=code):
                    contract = _resolve_contract(api, code)
                    api.quote.unsubscribe(
                        contract,
                        quote_type=sj.constant.QuoteType.BidAsk,
                        version=sj.constant.QuoteVersion.v1,
                    )
                _run(work)
                print(f"[bidask] 閒置退訂 {code}")
            except Exception as e:
                print(f"[bidask] 退訂 {code} 失敗（忽略）: {e}")
            _bidask_subscribed.pop(code, None)
            _bidask_cache.pop(code, None)


if __name__ == "__main__":
    threading.Thread(target=_bidask_sweep_worker, daemon=True, name="bidask-sweep").start()
    app.run(host="127.0.0.1", port=5455)
