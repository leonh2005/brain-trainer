"""lib/yf_guard 的單元測試。

重點不是驗證 yfinance 內部行為，而是確認三個分支都不會讓服務掛掉：
  1. 未初始化就呼叫 close（安全 no-op）
  2. 沒裝 yfinance（chip-tracker）要靜默、回 False
  3. 有裝 yfinance（其餘五個服務）旗標要一致，register 要真的掛上 teardown
"""

import yf_guard


def test_flags_consistent_with_installation():
    """裝了 yfinance 時兩個旗標都該是 True；沒裝時 AVAILABLE 必須 False。"""
    if yf_guard.YFINANCE_INSTALLED:
        assert yf_guard.AVAILABLE is True, (
            "yfinance 已安裝但 yfinance.cache 沒有 _TzDBManager/_CookieDBManager，"
            "清理會靜默失效"
        )
    else:
        assert yf_guard.AVAILABLE is False


def test_close_is_safe_without_prior_initialisation():
    """從未初始化快取時呼叫也要安全，不可拋例外。"""
    yf_guard.close_yf_caches()
    yf_guard.close_yf_caches()


def test_close_returns_bool():
    assert isinstance(yf_guard.close_yf_caches(), bool)


def test_register_attaches_teardown():
    from flask import Flask

    app = Flask(__name__)
    yf_guard.register(app)

    assert app.teardown_request_funcs, "register() 沒有掛上 teardown_request"


def test_register_is_repeatable():
    """同一 app 重複 register 不該拋例外（熱重載或測試重跑時會遇到）。"""
    from flask import Flask

    app = Flask(__name__)
    yf_guard.register(app)
    yf_guard.register(app)
