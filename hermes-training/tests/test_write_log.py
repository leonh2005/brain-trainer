import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from write_log import write_log_entry


def test_write_log_entry_without_lesson(tmp_path):
    write_log_entry(
        date_str="2026-08-09",
        prompt="今天天氣如何",
        hermes_initial="不知道",
        lesson=None,
        hermes_revised=None,
        log_dir=tmp_path,
    )
    content = (tmp_path / "2026-08-09.md").read_text(encoding="utf-8")
    assert "今天天氣如何" in content
    assert "不知道" in content
    assert "教材" not in content


def test_write_log_entry_with_lesson_appends_to_same_file(tmp_path):
    write_log_entry("2026-08-09", "問題一", "初答一", None, None, tmp_path)
    write_log_entry("2026-08-09", "問題二", "初答二", "教材二", "修正後二", tmp_path)
    content = (tmp_path / "2026-08-09.md").read_text(encoding="utf-8")
    assert "問題一" in content
    assert "問題二" in content
    assert "教材二" in content
    assert "修正後二" in content
    assert content.index("問題一") < content.index("問題二")


def test_write_log_entry_with_clarify_rounds(tmp_path):
    write_log_entry(
        date_str="2026-08-09",
        prompt="問題三",
        hermes_initial="最終答案",
        lesson=None,
        hermes_revised=None,
        log_dir=tmp_path,
        clarify_rounds=[
            {"hermes_question": "你說的是哪個？", "trainer_answer": "我說的是 X"},
        ],
    )
    content = (tmp_path / "2026-08-09.md").read_text(encoding="utf-8")
    assert "互動釐清" in content
    assert "你說的是哪個？" in content
    assert "我說的是 X" in content
    assert content.index("互動釐清") < content.index("Hermes 初答")
