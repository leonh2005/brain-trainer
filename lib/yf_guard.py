"""yfinance peewee 快取連線清理（跨服務共用）。

背景
----
yfinance 的 tz 與 cookie 快取都走 peewee，而 peewee 的 SQLite 連線是 thread-local。
Flask 每個請求開一條新執行緒，連線在執行緒結束後不會自動關閉，fd 就一直累積
（2026-10-27 起多個服務實測到 tkr-tz.db fd 累積後出現「Too many open files」）。

更關鍵的是 `yf.download()` 的 `threads` 參數預設為 True，即使只有一個 ticker，
yfinance 也會走 `_multitasking` 在 worker thread 執行下載，連線因此開在 worker thread；
請求 thread 的 teardown 呼叫 `close_db()` 只關得到自己 thread 的連線，對它完全無效。
2026-11-06 實測：3 次 threads=True 後 fd +16，呼叫 close_db() 後仍是 132 → 132。

所以呼叫端必須：
  1. 對 `yf.download()` 明確傳 `threads=False`，讓連線留在請求 thread；
  2. 請求結束時呼叫本模組的 `close_yf_caches()`。
`yf.Ticker(...).history()` 走的是同步路徑（PriceHistory），不需要這個處理。

兩種「不能清理」要分開看
------------------------
- 服務根本沒裝 yfinance（chip-tracker）：沒有 tkr-tz.db 可漏，完全靜默。
- 裝了但 yfinance 改版拿掉 `_TzDBManager` / `_CookieDBManager`：這是故障，
  清理會失效、fd 會重新累積，所以記一次 warning 留線索（只記一次，不洗版）。
"""

import logging

_log = logging.getLogger(__name__)
_warned = False

try:
    import yfinance  # noqa: F401

    YFINANCE_INSTALLED = True
except Exception:
    YFINANCE_INSTALLED = False

if YFINANCE_INSTALLED:
    try:
        from yfinance.cache import _CookieDBManager, _TzDBManager

        AVAILABLE = True
    except Exception:  # 私有 API 改名或移除
        _CookieDBManager = _TzDBManager = None
        AVAILABLE = False
else:
    _CookieDBManager = _TzDBManager = None
    AVAILABLE = False


def close_yf_caches() -> bool:
    """關閉當前執行緒的 tz / cookie 兩條快取連線。

    未初始化時是安全 no-op（peewee 下次查詢會自動重連）。
    回傳兩條連線是否都關閉成功；沒裝 yfinance 時回 False。
    """
    global _warned
    if not AVAILABLE:
        if YFINANCE_INSTALLED and not _warned:
            _warned = True
            _log.warning(
                "yfinance 已安裝但 yfinance.cache 沒有 _TzDBManager/_CookieDBManager，"
                "快取清理停用，fd 可能重新累積（請檢查 yfinance 版本）"
            )
        return False

    ok = True
    for manager in (_TzDBManager, _CookieDBManager):
        try:
            manager.close_db()
        except Exception:
            ok = False
            if not _warned:
                _warned = True
                _log.warning("關閉 yfinance 快取連線失敗", exc_info=True)
    return ok


def register(app) -> None:
    """把清理掛到 Flask app 的 teardown，呼叫端一行搞定。"""
    app.teardown_request(lambda exc=None: close_yf_caches())
