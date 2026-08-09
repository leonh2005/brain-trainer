import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_scheduled_query_tasks import build_scheduled_query_tasks


def test_lottery_tasks_have_reference_answer():
    with patch("extract_scheduled_query_tasks.get_jackpots", return_value={5118: 5.2, 5134: 2.1}), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=[]), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value="報告內容"):
        tasks = build_scheduled_query_tasks()
    lotto649 = next(t for t in tasks if "大樂透" in t["prompt"])
    assert "5.2" in lotto649["claude_answer"]
    assert "會" in lotto649["claude_answer"]  # 5.2 億 >= 4 億門檻，會推播
    lotto638 = next(t for t in tasks if "威力彩" in t["prompt"])
    assert "2.1" in lotto638["claude_answer"]
    assert "不會" in lotto638["claude_answer"]  # 2.1 億 < 4 億門檻


def test_steam_task_with_no_free_games():
    with patch("extract_scheduled_query_tasks.get_jackpots", return_value={}), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=[]), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value=""):
        tasks = build_scheduled_query_tasks()
    steam = next(t for t in tasks if "Steam" in t["prompt"])
    assert "沒有" in steam["claude_answer"]


def test_steam_task_with_free_games():
    games = [{"name": "遊戲A"}, {"name": "遊戲B"}]
    with patch("extract_scheduled_query_tasks.get_jackpots", return_value={}), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=games), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value=""):
        tasks = build_scheduled_query_tasks()
    steam = next(t for t in tasks if "Steam" in t["prompt"])
    assert "遊戲A" in steam["claude_answer"]
    assert "遊戲B" in steam["claude_answer"]


def test_hourly_report_task_uses_report_text_verbatim():
    with patch("extract_scheduled_query_tasks.get_jackpots", return_value={}), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=[]), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value="報告內容"):
        tasks = build_scheduled_query_tasks()
    hourly = next(t for t in tasks if "每小時" in t["prompt"])
    assert hourly["claude_answer"] == "報告內容"


def test_food_expiry_and_vm_health_have_no_reference_answer():
    with patch("extract_scheduled_query_tasks.get_jackpots", return_value={}), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=[]), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value=""):
        tasks = build_scheduled_query_tasks()
    food = next(t for t in tasks if "食物" in t["prompt"])
    vm = next(t for t in tasks if "VM" in t["prompt"])
    assert "claude_answer" not in food
    assert "claude_answer" not in vm


def test_fetch_failure_skips_that_task_only():
    with patch("extract_scheduled_query_tasks.get_jackpots", side_effect=RuntimeError("API 失敗")), \
         patch("extract_scheduled_query_tasks.fetch_free_games", return_value=[]), \
         patch("extract_scheduled_query_tasks.get_hourly_report", return_value="報告內容"):
        tasks = build_scheduled_query_tasks()
    prompts = [t["prompt"] for t in tasks]
    assert not any("大樂透" in p or "威力彩" in p for p in prompts)
    assert any("每小時" in p for p in prompts)  # 其他項目不受影響
