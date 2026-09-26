import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_escape_helper_exists():
    src = (STATIC / "escape.js").read_text(encoding="utf-8")
    assert "escapeHtml" in src


def test_index_js_does_not_use_innerhtml_with_data():
    """首頁 JS 不得用 innerHTML 直接插入伺服器資料。"""
    src = (STATIC / "index.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src


def test_index_js_poll_is_guarded_and_self_healing():
    """輪詢必須有 in-flight 守衛與錯誤處理。

    沒有守衛時，某次回應一旦超過 5 秒，下一輪不會等待，請求就無上限堆積；
    沒有 catch 時，服務重啟會每 5 秒噴一次 unhandled rejection，畫面還無聲
    凍結在最後一次成功的狀態。finally 負責把守衛放掉，否則一次失敗就讓輪詢
    永久停擺，失去自我修復。
    """
    src = (STATIC / "index.js").read_text(encoding="utf-8")
    assert "inflight" in src
    assert "catch" in src
    assert "finally" in src


def test_templates_have_utf8_charset():
    for name in ("index.html", "domain.html"):
        html = (Path(__file__).resolve().parent.parent / "templates" / name).read_text(encoding="utf-8")
        assert re.search(r'<meta\s+charset="utf-8"', html, re.I), f"{name} 缺少 meta charset"
