"""把一條任務的完整訓練記錄（提問／初答／教材／修正後回答）寫進當天 log 檔。"""
import json
import sys
from pathlib import Path

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"


def write_log_entry(date_str: str, prompt: str, hermes_initial: str,
                     lesson, hermes_revised, log_dir: Path = DEFAULT_LOG_DIR,
                     clarify_rounds: list | None = None) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{date_str}.md"

    lines = ["## 任務", prompt.strip(), ""]
    if clarify_rounds:
        lines.append("### 互動釐清")
        for i, round_ in enumerate(clarify_rounds, 1):
            lines += [
                f"{i}. Hermes 問：{round_['hermes_question']}",
                f"   訓練者答：{round_['trainer_answer']}",
            ]
        lines.append("")
    lines += [
        "### Hermes 初答",
        hermes_initial.strip(),
        "",
    ]
    if lesson:
        lines += [
            "### 教材",
            lesson.strip(),
            "",
            "### Hermes 修正後回答",
            (hermes_revised or "").strip(),
            "",
        ]
    lines.append("---\n")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        date_str = payload["date"]
        prompt = payload["prompt"]
        hermes_initial = payload["hermes_initial"]
    except (json.JSONDecodeError, KeyError):
        print(
            'Usage: echo \'{"date":"...","prompt":"...","hermes_initial":"..."}\' | python3 write_log.py',
            file=sys.stderr,
        )
        sys.exit(1)
    lesson = payload.get("lesson")
    hermes_revised = payload.get("hermes_revised")
    clarify_rounds = payload.get("clarify_rounds")
    write_log_entry(date_str, prompt, hermes_initial, lesson, hermes_revised,
                     clarify_rounds=clarify_rounds)
    print("已寫入 log")


if __name__ == "__main__":
    main()
