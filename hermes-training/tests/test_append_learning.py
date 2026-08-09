import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from append_learning import append_learning


def test_append_learning_creates_file_with_dated_entry(tmp_path):
    path = tmp_path / "learnings.md"
    append_learning("Hermes 回答太簡短，要多給脈絡", "2026-08-09", path)
    content = path.read_text(encoding="utf-8")
    assert "## 2026-08-09" in content
    assert "Hermes 回答太簡短，要多給脈絡" in content


def test_append_learning_appends_without_overwriting(tmp_path):
    path = tmp_path / "learnings.md"
    append_learning("第一條教材", "2026-08-09", path)
    append_learning("第二條教材", "2026-08-10", path)
    content = path.read_text(encoding="utf-8")
    assert "第一條教材" in content
    assert "第二條教材" in content
    assert content.index("第一條教材") < content.index("第二條教材")
