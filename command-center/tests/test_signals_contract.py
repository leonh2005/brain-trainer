"""SIGNALS 回傳契約測試。

2026-11-28 事故：新加的 holiday-guard 忘了 @_wrap，API 回 200 但前端
`loadCard` 的 `res.ok` 是 undefined，卡片顯示「讀取失敗」。
curl 看不出來，要看 dump-dom 或這支測試。
"""

import sources


def test_every_signal_is_wrapped():
    """每個 SIGNAL 都必須被 @_wrap 包過，否則前端拿不到 {ok, data, updated}。"""
    for name, fn in sources.SIGNALS.items():
        assert fn is not None, f"{name}: SIGNALS 裡是 None"
        assert fn.__qualname__ == "_wrap.<locals>.inner", (
            f"{name}: 缺少 @_wrap（qualname={fn.__qualname__}）。"
            "前端 loadCard 會因 res.ok 為 undefined 顯示「讀取失敗」"
        )


def test_wrap_envelope_shape():
    """_wrap 的輸出必須是 {ok: True, data, updated}。"""

    @sources._wrap
    def sample():
        return {"x": 1}, "2026-11-28 12:00"

    assert sample() == {"ok": True, "data": {"x": 1}, "updated": "2026-11-28 12:00"}


def test_wrap_catches_exception():
    """_wrap 必須把例外轉成 {ok: False, error}，不能讓卡片整個爆掉。"""

    @sources._wrap
    def broken():
        raise ValueError("boom")

    result = broken()
    assert result["ok"] is False
    assert "ValueError" in result["error"]
    assert result["updated"] == ""
