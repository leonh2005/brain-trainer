"""擷取當天 Claude Code session transcript 中的純問答任務，供 Hermes 夜間訓練使用。

只保留完全沒有寫入/修改/搬移動作的任務——任何 Edit/Write/NotebookEdit 呼叫，
或不在安全白名單內的 Bash 指令，整條任務都會被排除（fail closed）。
"""
import json
import re
import sys

MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit"}

SAFE_BASH_PREFIXES = [
    re.compile(r"^ls\b"),
    re.compile(r"^cat\b"),
    re.compile(r"^grep\b"),
    re.compile(r"^head\b"),
    re.compile(r"^tail\b"),
    re.compile(r"^wc\b"),
    re.compile(r"^pwd\b"),
    re.compile(r"^which\b"),
    re.compile(r"^echo\b"),
    re.compile(r"^git\s+(status|diff|log|show|branch)\b"),
    re.compile(r"^find\b"),
]

UNSAFE_FIND_FLAGS = re.compile(r"-delete|-exec")


def is_bash_command_safe(command: str) -> bool:
    """白名單制：每個以 &&/;/| 分隔的片段都必須命中安全前綴，且 find 不可帶 -delete/-exec。"""
    segments = [seg.strip() for seg in re.split(r"&&|;|\|", command) if seg.strip()]
    if not segments:
        return False
    for seg in segments:
        if not any(p.match(seg) for p in SAFE_BASH_PREFIXES):
            return False
        if seg.startswith("find") and UNSAFE_FIND_FLAGS.search(seg):
            return False
    return True


def _task_is_mutating(assistant_entries: list) -> bool:
    for entry in assistant_entries:
        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name in MUTATING_TOOLS:
                return True
            if name == "Bash":
                command = block.get("input", {}).get("command", "")
                if not is_bash_command_safe(command):
                    return True
    return False


def _extract_user_text(entry: dict):
    content = entry.get("message", {}).get("content")
    if isinstance(content, str) and not content.startswith("<"):
        return content
    return None


def _extract_final_answer_text(assistant_entries: list) -> str:
    if not assistant_entries:
        return ""
    last = assistant_entries[-1]
    content = last.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def extract_tasks(transcript_path: str) -> list:
    tasks = []
    current_user_text = None
    current_assistant_entries = []

    def flush():
        if current_user_text is not None and current_assistant_entries:
            if not _task_is_mutating(current_assistant_entries):
                answer = _extract_final_answer_text(current_assistant_entries)
                if answer:
                    tasks.append({"prompt": current_user_text, "claude_answer": answer})

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            entry_type = entry.get("type")
            if entry_type == "user":
                text = _extract_user_text(entry)
                if text is not None:
                    flush()
                    current_user_text = text
                    current_assistant_entries = []
            elif entry_type == "assistant" and current_user_text is not None:
                current_assistant_entries.append(entry)
    flush()
    return tasks


def extract_tasks_from_paths(paths: list) -> list:
    all_tasks = []
    for path in paths:
        all_tasks.extend(extract_tasks(path))
    return all_tasks


def main():
    if len(sys.argv) < 2:
        print("Usage: extract_tasks.py <transcript.jsonl> [more.jsonl ...]", file=sys.stderr)
        sys.exit(1)
    tasks = extract_tasks_from_paths(sys.argv[1:])
    json.dump(tasks, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
