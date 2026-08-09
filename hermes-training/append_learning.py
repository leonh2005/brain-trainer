"""把一條新教材加進 Hermes 的累積學習檔（~/.hermes/learnings.md）。"""
import json
import sys
from pathlib import Path

DEFAULT_PATH = Path.home() / ".hermes" / "learnings.md"


def append_learning(lesson: str, date_str: str, path: Path = DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = f"\n## {date_str}\n\n{lesson.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        date_str = payload["date"]
        lesson = payload["lesson"]
    except (json.JSONDecodeError, KeyError):
        print('Usage: echo \'{"date": "2026-08-09", "lesson": "..."}\' | python3 append_learning.py', file=sys.stderr)
        sys.exit(1)
    append_learning(lesson, date_str)
    print("已寫入 learnings.md")


if __name__ == "__main__":
    main()
