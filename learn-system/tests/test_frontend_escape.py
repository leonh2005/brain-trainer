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


def test_templates_have_utf8_charset():
    for name in ("index.html", "domain.html"):
        html = (Path(__file__).resolve().parent.parent / "templates" / name).read_text(encoding="utf-8")
        assert re.search(r'<meta\s+charset="utf-8"', html, re.I), f"{name} 缺少 meta charset"
