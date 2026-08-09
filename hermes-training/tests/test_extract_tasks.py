import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_tasks import extract_tasks, is_bash_command_safe, extract_tasks_from_paths


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_text(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _assistant_tool(name, input_dict):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": input_dict}
    ]}}


def test_plain_text_task_is_included(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("今天天氣如何"),
        _assistant_text("今天晴天"),
    ])
    tasks = extract_tasks(str(p))
    assert tasks == [{"prompt": "今天天氣如何", "claude_answer": "今天晴天"}]


def test_task_with_edit_tool_is_excluded(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("改一下這個檔案"),
        _assistant_tool("Edit", {"file_path": "x.py"}),
        _assistant_text("改好了"),
    ])
    assert extract_tasks(str(p)) == []


def test_task_with_safe_bash_is_included(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("列出檔案"),
        _assistant_tool("Bash", {"command": "ls -la"}),
        _assistant_text("這是檔案清單"),
    ])
    tasks = extract_tasks(str(p))
    assert tasks == [{"prompt": "列出檔案", "claude_answer": "這是檔案清單"}]


def test_task_with_mutating_bash_is_excluded(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("清一下暫存"),
        _assistant_tool("Bash", {"command": "rm -rf /tmp/foo"}),
        _assistant_text("清好了"),
    ])
    assert extract_tasks(str(p)) == []


def test_task_with_unknown_bash_is_excluded_fail_closed(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("跑個腳本"),
        _assistant_tool("Bash", {"command": "python3 some_unknown_script.py"}),
        _assistant_text("跑完了"),
    ])
    assert extract_tasks(str(p)) == []


def test_meta_and_command_messages_are_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        _user("<local-command-caveat>ignore me</local-command-caveat>"),
        _user("<command-name>/clear</command-name>"),
        _user("真正的問題"),
        _assistant_text("真正的答案"),
    ])
    tasks = extract_tasks(str(p))
    assert tasks == [{"prompt": "真正的問題", "claude_answer": "真正的答案"}]


def test_task_without_assistant_reply_is_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_user("沒人回答我")])
    assert extract_tasks(str(p)) == []


def test_is_bash_command_safe_whitelist():
    assert is_bash_command_safe("ls -la") is True
    assert is_bash_command_safe("git status") is True
    assert is_bash_command_safe("git diff HEAD~1") is True
    assert is_bash_command_safe("ls && rm -rf /") is False
    assert is_bash_command_safe("find . -name '*.py' -delete") is False
    assert is_bash_command_safe("curl -X POST http://x") is False


def test_is_bash_command_safe_false_positive_multi_segment():
    assert is_bash_command_safe("ls -la && git status") is True


def test_is_bash_command_safe_blocks_redirection():
    assert is_bash_command_safe("echo bad > file") is False
    assert is_bash_command_safe("echo hi >> ~/.zshrc") is False


def test_is_bash_command_safe_blocks_multiline():
    assert is_bash_command_safe("ls\nrm -rf /tmp/x") is False


def test_is_bash_command_safe_blocks_command_substitution():
    assert is_bash_command_safe("echo `rm -rf /tmp/x`") is False
    assert is_bash_command_safe("echo $(rm -rf /tmp/x)") is False


def test_is_bash_command_safe_blocks_background():
    assert is_bash_command_safe("ls & rm -rf /tmp/x") is False


def test_is_bash_command_safe_blocks_extended_find_write_flags():
    assert is_bash_command_safe("find . -fprintf /etc/passwd %p") is False


def test_extract_tasks_from_paths_merges_multiple_files(tmp_path):
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    _write_jsonl(p1, [_user("問題一"), _assistant_text("答案一")])
    _write_jsonl(p2, [_user("問題二"), _assistant_text("答案二")])
    tasks = extract_tasks_from_paths([str(p1), str(p2)])
    assert tasks == [
        {"prompt": "問題一", "claude_answer": "答案一"},
        {"prompt": "問題二", "claude_answer": "答案二"},
    ]
