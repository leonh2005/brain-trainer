# Hermes 夜間訓練管線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每晚 1 點自動把當天 Steven 交給 Claude Code 的純問答類任務重跑給 Hermes，比對差距、寫教材、驗證改善，並把教材累加進 Hermes 的 system prompt。

**Architecture:** 一組獨立可測試的 Python 小工具（擷取任務、呼叫 Hermes、寫教材、寫 log、同步 config）+ 一份給 headless `claude -p` 的訓練指令模板，由 shell script 在 LaunchAgent 觸發下串接起來。判斷「差距多大、怎麼教」的推理工作交給當晚跑的 headless Claude 本身完成，其餘皆為確定性程式碼。

**Tech Stack:** Python 3（PyYAML、pytest 已確認可用）、Bash、LaunchAgent（launchd）、Claude Code CLI headless 模式。所有結果只寫本機摘要檔，不推播 Telegram（沿用 `scripts/gdrive_sort.sh` 的 headless `claude -p` 呼叫慣例，但拿掉推播部分）。

## Global Constraints

- 安全紅線：任何任務只要出現 `Edit`／`Write`／`NotebookEdit` 工具呼叫，或 Bash 指令不在明確安全白名單內，整條任務排除，不進訓練集（寧可少教，不可教錯）
- Hermes 完全不執行寫入/修改/搬移類任務，訓練全程只做文字問答比對
- 教材用「累加進 `~/.hermes/learnings.md`，同步進 `config.yaml` 的 `zhtw` personality」方式生效，不做 RAG
- 沿用既有 `scripts/gdrive_sort.sh` 的 headless `claude -p` 呼叫慣例（`--permission-mode acceptEdits --allowedTools "Read,Bash,Grep,Glob" --disallowedTools "Agent,Workflow,Write,Edit"`），但不推播 Telegram，所有結果只寫本機摘要檔
- 沿用 spec：`docs/superpowers/specs/2026-08-09-hermes-nightly-training-design.md`

---

### Task 1: extract_tasks.py — 擷取並過濾當天任務

**Files:**
- Create: `hermes-training/extract_tasks.py`
- Test: `hermes-training/tests/test_extract_tasks.py`

**Interfaces:**
- Produces: `extract_tasks(transcript_path: str) -> list[dict]`，每個 dict 為 `{"prompt": str, "claude_answer": str}`
- Produces: CLI `python3 extract_tasks.py <transcript.jsonl> [more.jsonl ...]`，stdout 輸出合併後的 JSON array

- [ ] **Step 1: 建立測試用的假 transcript fixture 並寫失敗測試**

```python
# hermes-training/tests/test_extract_tasks.py
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
```

- [ ] **Step 2: 執行測試確認失敗（模組還不存在）**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_extract_tasks.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'extract_tasks'`

- [ ] **Step 3: 實作 extract_tasks.py**

```python
# hermes-training/extract_tasks.py
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_extract_tasks.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject
git add hermes-training/extract_tasks.py hermes-training/tests/test_extract_tasks.py
git commit -m "feat: extract_tasks.py 擷取並過濾 Hermes 夜間訓練任務"
```

---

### Task 2: call_hermes.py — 呼叫 Hermes 取得回答

**Files:**
- Create: `hermes-training/call_hermes.py`
- Test: `hermes-training/tests/test_call_hermes.py`

**Interfaces:**
- Produces: `call_hermes(prompt: str, timeout: int = 120) -> str`
- Produces: `HermesCallError(RuntimeError)`
- Produces: CLI，從 stdin 讀取整段 prompt 文字，stdout 印出 Hermes 回答；`echo "$PROMPT" | python3 call_hermes.py`

- [ ] **Step 1: 寫失敗測試（mock subprocess）**

```python
# hermes-training/tests/test_call_hermes.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from call_hermes import call_hermes, HermesCallError


def test_call_hermes_returns_stdout_on_success():
    fake_result = MagicMock(returncode=0, stdout="這是 Hermes 的回答\n", stderr="")
    with patch("call_hermes.subprocess.run", return_value=fake_result) as mock_run:
        answer = call_hermes("問題")
        assert answer == "這是 Hermes 的回答"
        args, kwargs = mock_run.call_args
        assert args[0] == ["hermes", "-z", "問題"]


def test_call_hermes_raises_on_nonzero_exit():
    fake_result = MagicMock(returncode=1, stdout="", stderr="ollama 未啟動")
    with patch("call_hermes.subprocess.run", return_value=fake_result):
        try:
            call_hermes("問題")
            assert False, "should have raised"
        except HermesCallError as e:
            assert "ollama 未啟動" in str(e)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_call_hermes.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'call_hermes'`

- [ ] **Step 3: 實作 call_hermes.py**

```python
# hermes-training/call_hermes.py
"""呼叫本機 Hermes CLI（headless -z 模式）取得單次回答。"""
import subprocess
import sys


class HermesCallError(RuntimeError):
    pass


def call_hermes(prompt: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["hermes", "-z", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise HermesCallError(f"hermes 執行失敗 (exit={result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def main():
    prompt = sys.stdin.read().strip()
    if not prompt:
        print("Usage: echo '<prompt>' | python3 call_hermes.py", file=sys.stderr)
        sys.exit(1)
    try:
        print(call_hermes(prompt))
    except HermesCallError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_call_hermes.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject
git add hermes-training/call_hermes.py hermes-training/tests/test_call_hermes.py
git commit -m "feat: call_hermes.py 封裝 Hermes headless 呼叫"
```

---

### Task 3: append_learning.py — 累加教材

**Files:**
- Create: `hermes-training/append_learning.py`
- Test: `hermes-training/tests/test_append_learning.py`

**Interfaces:**
- Consumes: 無（獨立工具）
- Produces: `append_learning(lesson: str, date_str: str, path) -> None`
- Produces: CLI，從 stdin 讀取 JSON `{"date": "...", "lesson": "..."}`，寫入 `~/.hermes/learnings.md`

- [ ] **Step 1: 寫失敗測試**

```python
# hermes-training/tests/test_append_learning.py
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_append_learning.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'append_learning'`

- [ ] **Step 3: 實作 append_learning.py**

```python
# hermes-training/append_learning.py
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_append_learning.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject
git add hermes-training/append_learning.py hermes-training/tests/test_append_learning.py
git commit -m "feat: append_learning.py 累加 Hermes 教材"
```

---

### Task 4: write_log.py — 寫每日訓練 log

**Files:**
- Create: `hermes-training/write_log.py`
- Test: `hermes-training/tests/test_write_log.py`

**Interfaces:**
- Produces: `write_log_entry(date_str: str, prompt: str, hermes_initial: str, lesson: str | None, hermes_revised: str | None, log_dir, clarify_rounds: list | None = None) -> None`
- Produces: CLI，從 stdin 讀取 JSON `{"date", "prompt", "hermes_initial", "lesson", "hermes_revised", "clarify_rounds"}`（`lesson`/`hermes_revised`/`clarify_rounds` 可省略），寫入 `hermes-training/logs/<date>.md`
- `clarify_rounds`（若有）是一份 list，每個元素為 `{"hermes_question": str, "trainer_answer": str}`，代表 Hermes 問不懂、訓練者回答教它的每一輪來回（最多 4 輪，見 Task 6）

- [ ] **Step 1: 寫失敗測試**

```python
# hermes-training/tests/test_write_log.py
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_write_log.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'write_log'`

- [ ] **Step 3: 實作 write_log.py**

```python
# hermes-training/write_log.py
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_write_log.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject
git add hermes-training/write_log.py hermes-training/tests/test_write_log.py
git commit -m "feat: write_log.py 寫入每日訓練 log"
```

---

### Task 5: sync_learnings.py — 同步教材進 Hermes config

**Files:**
- Create: `hermes-training/sync_learnings.py`
- Test: `hermes-training/tests/test_sync_learnings.py`

**Interfaces:**
- Produces: `sync_learnings(config_path, learnings_path) -> None`
- Produces: CLI，`python3 sync_learnings.py`（用預設路徑 `~/.hermes/config.yaml`、`~/.hermes/learnings.md`）

**設計重點：** 用固定分隔字串 `MARKER` 把 `zhtw` personality 切成「原本的語言規則（保留使用者手動編輯）」與「自動同步的教材區塊」兩段，每次同步都只重建教材區塊，避免重複疊加或洗掉手動修改的部分。

- [ ] **Step 1: 寫失敗測試**

```python
# hermes-training/tests/test_sync_learnings.py
import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sync_learnings import sync_learnings, MARKER


def _write_config(path, zhtw_value):
    data = {
        "model": {"default": "qwen3-tw"},
        "agent": {"personalities": {"zhtw": zhtw_value}},
        "display": {"personality": "zhtw"},
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_sync_learnings_appends_learnings_to_zhtw(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"
    _write_config(config_path, "永遠用繁體中文回覆。")
    learnings_path.write_text("## 2026-08-09\n\nHermes 回答太簡短。\n", encoding="utf-8")

    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw.startswith("永遠用繁體中文回覆。")
    assert "Hermes 回答太簡短。" in zhtw
    assert data["model"]["default"] == "qwen3-tw"  # 其他 key 不受影響


def test_sync_learnings_is_idempotent(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"
    _write_config(config_path, "永遠用繁體中文回覆。")
    learnings_path.write_text("## 2026-08-09\n\n教材A\n", encoding="utf-8")

    sync_learnings(config_path, learnings_path)
    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw.count("教材A") == 1
    assert zhtw.count("永遠用繁體中文回覆。") == 1


def test_sync_learnings_with_no_learnings_file_keeps_base_only(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"  # 不存在
    _write_config(config_path, "永遠用繁體中文回覆。")

    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw == "永遠用繁體中文回覆。"
    assert MARKER not in zhtw
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_sync_learnings.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'sync_learnings'`

- [ ] **Step 3: 實作 sync_learnings.py**

```python
# hermes-training/sync_learnings.py
"""把累積的 Hermes 教材（learnings.md）同步進 config.yaml 的 zhtw personality，
讓 Hermes 每次對話都會帶著這些教材。"""
from pathlib import Path
import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"
DEFAULT_LEARNINGS_PATH = Path.home() / ".hermes" / "learnings.md"

MARKER = "\n\n---\n# Steven 訓練教材（自動同步，勿手動編輯此區塊）\n\n"


def sync_learnings(config_path: Path = DEFAULT_CONFIG_PATH,
                    learnings_path: Path = DEFAULT_LEARNINGS_PATH) -> None:
    config_path = Path(config_path)
    learnings_path = Path(learnings_path)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    current = config.get("agent", {}).get("personalities", {}).get("zhtw", "")
    base = current.split(MARKER)[0].rstrip()

    learnings = ""
    if learnings_path.exists():
        learnings = learnings_path.read_text(encoding="utf-8").strip()

    new_value = base if not learnings else base + MARKER + learnings

    config.setdefault("agent", {}).setdefault("personalities", {})["zhtw"] = new_value

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def main():
    sync_learnings()
    print("已同步 learnings.md 進 config.yaml")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_sync_learnings.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject
git add hermes-training/sync_learnings.py hermes-training/tests/test_sync_learnings.py
git commit -m "feat: sync_learnings.py 同步教材進 Hermes config"
```

---

### Task 6: 訓練指令模板 + run_nightly.sh 串接

**Files:**
- Create: `hermes-training/nightly_prompt_template.txt`
- Create: `hermes-training/run_nightly.sh`

**Interfaces:**
- Consumes: Task 1-5 的所有 CLI（`extract_tasks.py`、`call_hermes.py`、`append_learning.py`、`write_log.py`、`sync_learnings.py`）
- Produces: 可直接執行的 `run_nightly.sh`，供 Task 7 的 LaunchAgent 呼叫

此任務為 shell 串接與 LLM 指令撰寫，非確定性邏輯，不寫 unit test；用 Step 3 的手動 dry-run 驗證。

- [ ] **Step 1: 建立 nightly_prompt_template.txt**

```text
你現在是「Hermes 夜間訓練管線」，這是無人值守自動執行，不會有人跟你互動確認，不要詢問任何問題。

背景：Hermes 是 Steven 本機的 Ollama agent，目標是讓它逐漸學會像你一樣理解 Steven 並回答問題。
今天 Steven 交給你的純問答任務已經整理成一份 JSON 清單，存在：{{TASKS_FILE}}
清單格式：[{"prompt": "原始提問", "claude_answer": "你當時的回答"}, ...]

工作目錄：hermes-training/（相對於你目前 cwd）

請依序對清單中的每一條任務執行：

1. 用 Bash 呼叫：`echo "<prompt>" | python3 hermes-training/call_hermes.py`，取得 Hermes 對這條 prompt 的初始回答

1.5. **互動釐清迴圈（最多 4 輪）**：Hermes 是一次性呼叫、不會記得上次對話，所以每一輪都要自己把完整對話記錄組進新的 prompt 裡送出去。
   如果 Hermes 的回答是在反問你問題、或明確表示看不懂/無法回答，就把這輪視為「需要澄清」：
   - 用繁體中文寫下你的回答/教學，記錄成 `{"hermes_question": "<Hermes 問的>", "trainer_answer": "<你教它的>"}`（累積進這條任務的 `clarify_rounds` 清單）
   - 組一個新 prompt，格式：「以下是這條任務目前為止的完整對話記錄：\n[原始問題]：<prompt>\n[Hermes]：<上一輪回答>\n[訓練者]：<你的回答/教學>\n\n請根據以上脈絡，重新回答原始問題。」
   - 用 Bash 呼叫 `echo "<新 prompt>" | python3 hermes-training/call_hermes.py` 取得下一輪回答
   - 重複以上，最多 4 輪。4 輪後 Hermes 仍答不出來就放棄，直接把最後一輪的回答當作這條任務的初答，正常往下走（不要卡住整條訓練管線）
   - 如果 Hermes 一開始就正常回答（不是反問、不是表示無法回答），這個迴圈直接跳過，`clarify_rounds` 留空

2. 比較 Hermes 最終初答（若有經過 1.5 的迴圈，取迴圈結束後的最後一輪回答）跟 claude_answer 的差距。語意/建議方向大致一致就跳過這條，不用寫教材，直接記 log（教材欄位留空）
3. 差距明顯的，用繁體中文寫一條教材，具體說明「Hermes 少了什麼、為什麼你當時會那樣答」。用 Bash 呼叫（用 heredoc 避免轉義問題）：
   ```
   python3 hermes-training/append_learning.py <<'EOF'
   {"date": "{{DATE_STR}}", "lesson": "<你寫的教材>"}
   EOF
   ```
4. 把教材附加在原問題前重新問一次 Hermes（同樣呼叫 call_hermes.py），取得修正後回答，驗證有沒有改善
5. 用 Bash 呼叫（heredoc）把這條任務的完整記錄寫進當天 log：
   ```
   python3 hermes-training/write_log.py <<'EOF'
   {"date": "{{DATE_STR}}", "prompt": "<原提問>", "hermes_initial": "<初答>", "lesson": "<教材或省略>", "hermes_revised": "<修正後回答或省略>", "clarify_rounds": [<1.5 迴圈累積的紀錄，若無則省略此欄位>]}
   EOF
   ```

全部任務跑完後：

6. 用 Bash 執行：`python3 hermes-training/sync_learnings.py`
7. 最後只輸出一段繁體中文簡短總結（不要有其他內容）：今晚比對了幾條任務、學了幾條新規則、差距最大的是哪一條任務及原因。這段總結只會被寫進本機的摘要檔案供 Steven 事後查看，不會推播出去

安全規則（不可違反）：
- 只能用 Bash 呼叫上述 hermes-training/ 目錄裡已經存在的 python 腳本，不准自己寫新腳本、不准使用 Edit 或 Write 工具
- 不要修改清單以外的任何檔案
```

- [ ] **Step 2: 建立 run_nightly.sh**

```bash
#!/bin/bash
# Hermes 夜間訓練管線 — 每晚 01:00 由 LaunchAgent 觸發
# 把當天純問答任務重跑給 Hermes，比對差距、寫教材、同步進 Hermes system prompt
# 不推播 Telegram — 全部結果只寫進本機摘要檔，供 Steven 自行查看品質後再決定要不要開推播

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECT_DIR="$HOME/CCProject"
WORK_DIR="$PROJECT_DIR/hermes-training"
LOG="$PROJECT_DIR/logs/hermes_nightly_training.log"
DATE_STR="$(date '+%Y-%m-%d')"
TRANSCRIPT_DIR="$HOME/.claude/projects/-Users-steven-CCProject"
TASKS_FILE="$WORK_DIR/tasks_${DATE_STR}.json"
SUMMARY_FILE="$WORK_DIR/logs/summary_${DATE_STR}.md"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
write_summary() {
  mkdir -p "$WORK_DIR/logs"
  echo "$1" >> "$SUMMARY_FILE"
}

mapfile -t TRANSCRIPTS < <(find "$TRANSCRIPT_DIR" -maxdepth 1 -name "*.jsonl" -newermt "$DATE_STR 00:00:00" ! -newermt "$DATE_STR 23:59:59" 2>/dev/null)

if [ ${#TRANSCRIPTS[@]} -eq 0 ]; then
  log "今天沒有 session transcript，跳過"
  write_summary "🌙 Hermes 夜間訓練：今晚沒有可訓練的任務"
  exit 0
fi

python3 "$WORK_DIR/extract_tasks.py" "${TRANSCRIPTS[@]}" > "$TASKS_FILE" 2>>"$LOG"

TASK_COUNT=$(python3 -c "import json; print(len(json.load(open('$TASKS_FILE'))))" 2>>"$LOG")

if [ -z "$TASK_COUNT" ] || [ "$TASK_COUNT" -eq 0 ]; then
  log "過濾後沒有可訓練任務"
  write_summary "🌙 Hermes 夜間訓練：今晚 0 條可訓練任務（可能都涉及寫入/修改操作）"
  exit 0
fi

log "今晚可訓練任務數：$TASK_COUNT"

PROMPT="$(sed "s|{{TASKS_FILE}}|$TASKS_FILE|g; s|{{DATE_STR}}|$DATE_STR|g" "$WORK_DIR/nightly_prompt_template.txt")"

result=$(cd "$PROJECT_DIR" && timeout --kill-after=30 7200 claude -p "$PROMPT" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Bash,Grep,Glob" \
  --disallowedTools "Agent,Workflow,Write,Edit" \
  --output-format text 2>>"$LOG")
exit_code=$?

log "結果 (exit=$exit_code)：$result"

if [ "$exit_code" -eq 0 ] && [ -n "$result" ]; then
  write_summary "🌙 Hermes 夜間訓練完成 ($DATE_STR)

${result}"
else
  write_summary "⚠️ Hermes 夜間訓練失敗（exit=$exit_code），詳見 log：$LOG"
fi
```

- [ ] **Step 3: 賦予執行權限**

```bash
chmod +x ~/CCProject/hermes-training/run_nightly.sh
```

**⚠️ 不要在這一步手動執行 `run_nightly.sh` 做 dry-run。** 這支腳本會實際多次呼叫本機 Hermes（Ollama 模型），跑起來會吃滿 CPU/記憶體、拖慢整台 Mac。只確認語法正確即可：

```bash
bash -n ~/CCProject/hermes-training/run_nightly.sh
```

Expected: 沒有輸出（語法正確）。真正的端到端驗證留到 Task 7 的 LaunchAgent 在 01:00 自動觸發時進行，或由 Steven 決定何時手動在方便的時間點跑一次。

- [ ] **Step 4: Commit**

```bash
cd ~/CCProject
git add hermes-training/nightly_prompt_template.txt hermes-training/run_nightly.sh
git commit -m "feat: run_nightly.sh 串接 Hermes 夜間訓練管線"
```

---

### Task 7: LaunchAgent 排程

**Files:**
- Create: `~/Library/LaunchAgents/com.steven.hermes-nightly-training.plist`

**Interfaces:**
- Consumes: Task 6 的 `hermes-training/run_nightly.sh`

- [ ] **Step 1: 建立 plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.steven.hermes-nightly-training</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/steven/CCProject/hermes-training/run_nightly.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>1</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/steven/CCProject/logs/hermes_nightly_training.launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/steven/CCProject/logs/hermes_nightly_training.launchd.log</string>
</dict>
</plist>
```

- [ ] **Step 2: 載入 LaunchAgent**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.steven.hermes-nightly-training.plist
launchctl list | grep hermes-nightly-training
```

Expected: `launchctl list` 顯示該 Label 存在（PID 欄位可能是 `-`，代表尚未到觸發時間，正常）

- [ ] **Step 3: 驗證 plist 語法與排程正確性**

```bash
plutil -lint ~/Library/LaunchAgents/com.steven.hermes-nightly-training.plist
launchctl print gui/$(id -u)/com.steven.hermes-nightly-training | grep -A3 "calendar interval"
```

Expected: `plutil` 回報 OK；calendar interval 顯示 hour=1, minute=0

- [ ] **Step 4: Commit**

```bash
cd ~/CCProject
git add hermes-training/
git commit -m "chore: 新增 Hermes 夜間訓練 LaunchAgent 排程"
```

（plist 位於 `~/Library/LaunchAgents/`，不在 git 專案目錄內，不會被這次 commit 追蹤；此步驟只 commit `hermes-training/` 內若有遺漏的檔案。）

---

### Task 8: extract_scheduled_query_tasks.py — 併入既有排程查詢工作

**背景：** Steven 在 `telebot/scheduler.py`（跑在 Oracle VM 上）另外排程了幾個純程式碼查詢工作（樂透、Steam 限時免費遊戲、每小時報告、食物效期、VM 健康檢查），這些工作完全沒有 AI 介入，只是抓資料+格式化文字。Steven 要求把它們也併入 Hermes 的夜間訓練集。

**兩種類型：**
- **有正確答案可比對**（樂透×2、Steam、每小時報告）：這 4 個都是純 HTTP 查詢，Mac 本機可以直接呼叫對應函式取得即時資料當「正確答案」，走跟一般任務一樣的比對+教學流程
- **無正確答案，只記錄**（食物效期、VM 健康檢查）：這兩個需要 VM 上的即時資料（本機 sqlite/`/proc/meminfo`），Mac 本機拿不到。直接把問題丟給 Hermes、記錄它的回答，不比對、不寫教材，供 Steven 事後自行判斷回答品質

**Files:**
- Create: `hermes-training/extract_scheduled_query_tasks.py`
- Test: `hermes-training/tests/test_extract_scheduled_query_tasks.py`

**Interfaces:**
- Produces: `build_scheduled_query_tasks() -> list[dict]`，每個 dict 為 `{"prompt": str, "claude_answer": str}`（有正確答案的 4 項）或 `{"prompt": str}`（無正確答案的 2 項，故意不含 `claude_answer` key）
- Produces: CLI，`python3 extract_scheduled_query_tasks.py`，stdout 輸出 JSON array
- Consumes：從 `telebot` 目錄 import：
  - `lottery_monitor.get_jackpots() -> dict[int, float]`（gameCode 5118=大樂透、5134=威力彩，回傳億元）
  - `steam_monitor.fetch_free_games() -> list`（每個元素預期有 `name` 欄位；純查詢，不呼叫 `check_and_notify`，不會誤標記已讀、不會重複推播）
  - `hourly_monitor.get_hourly_report() -> str`（已經是格式化好的完整報告文字）

**安全規則：** 只呼叫上述三個純查詢函式，絕對不呼叫 `check_and_notify`、`save_seen`、或任何會發送 Telegram/LINE 訊息或寫入狀態檔的函式——這些函式有副作用，會影響 Steven 正式收到的通知。

- [ ] **Step 1: 寫失敗測試（mock telebot 模組函式）**

```python
# hermes-training/tests/test_extract_scheduled_query_tasks.py
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_extract_scheduled_query_tasks.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'extract_scheduled_query_tasks'`

- [ ] **Step 3: 實作 extract_scheduled_query_tasks.py**

```python
# hermes-training/extract_scheduled_query_tasks.py
"""把既有排程查詢工作（樂透、Steam、每小時報告、食物效期、VM 健康檢查）併入 Hermes 夜間訓練集。

只呼叫這些工作裡「純查詢」的函式，絕不呼叫會推播/寫入狀態的函式（如 check_and_notify），
避免干擾 Steven 正式收到的通知或誤標記狀態。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "CCProject" / "telebot"))

from lottery_monitor import get_jackpots
from steam_monitor import fetch_free_games
from hourly_monitor import get_hourly_report

LOTTO_THRESHOLD_YI = 4.0
LOTTO_GAMES = [(5118, "大樂透"), (5134, "威力彩")]


def _build_lottery_tasks() -> list:
    tasks = []
    try:
        jackpots = get_jackpots()
    except Exception:
        return tasks
    for code, name in LOTTO_GAMES:
        yi = jackpots.get(code)
        if yi is None:
            continue
        notify = "會" if yi >= LOTTO_THRESHOLD_YI else "不會"
        tasks.append({
            "prompt": f"{name}目前上看頭獎多少億？會不會推播通知？（門檻 {LOTTO_THRESHOLD_YI} 億）",
            "claude_answer": f"{name}目前上看頭獎 {yi:.1f} 億，{notify}推播（門檻 {LOTTO_THRESHOLD_YI} 億）",
        })
    return tasks


def _build_steam_task() -> list:
    try:
        games = fetch_free_games()
    except Exception:
        return []
    if games:
        names = "、".join(g["name"] for g in games)
        answer = f"目前 Steam 限時免費遊戲：{names}"
    else:
        answer = "目前沒有限時免費遊戲"
    return [{"prompt": "現在 Steam 有什麼限時免費遊戲？", "claude_answer": answer}]


def _build_hourly_report_task() -> list:
    try:
        report = get_hourly_report()
    except Exception:
        return []
    return [{"prompt": "現在的每小時追蹤報告是什麼？", "claude_answer": report}]


def build_scheduled_query_tasks() -> list:
    tasks = []
    tasks += _build_lottery_tasks()
    tasks += _build_steam_task()
    tasks += _build_hourly_report_task()
    # 這兩項需要 VM 上的即時資料，本機拿不到正確答案；
    # 直接把問題丟給 Hermes、記錄回答，不比對、不寫教材，供 Steven 事後自行判讀
    tasks.append({"prompt": "有哪些食物快過期了？"})
    tasks.append({"prompt": "Oracle VM 現在健康狀況如何？"})
    return tasks


def main():
    json.dump(build_scheduled_query_tasks(), sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/hermes-training && python3 -m pytest tests/test_extract_scheduled_query_tasks.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 把輸出併入 run_nightly.sh 的任務清單**

修改 `hermes-training/run_nightly.sh`，在 `extract_tasks.py` 產出 `$TASKS_FILE` 之後，合併 `extract_scheduled_query_tasks.py` 的輸出：

在這一行之後：
```bash
python3 "$WORK_DIR/extract_tasks.py" "${TRANSCRIPTS[@]}" > "$TASKS_FILE" 2>>"$LOG"
```
插入：
```bash
python3 "$WORK_DIR/extract_scheduled_query_tasks.py" > "$WORK_DIR/scheduled_tasks_${DATE_STR}.json" 2>>"$LOG"
python3 -c "
import json
a = json.load(open('$TASKS_FILE'))
b = json.load(open('$WORK_DIR/scheduled_tasks_${DATE_STR}.json'))
json.dump(a + b, open('$TASKS_FILE', 'w'), ensure_ascii=False, indent=2)
" 2>>"$LOG"
```

同時更新 `hermes-training/nightly_prompt_template.txt` 開頭的清單格式說明，加一句：「部分任務可能沒有 `claude_answer` 欄位（代表這條沒有正確答案可比對）——這種任務只需要正常呼叫 call_hermes.py 取得回答、走 1.5 的互動釐清迴圈（如適用），然後直接記 log（跳過步驟 2-4 的比對與教學，`lesson`/`hermes_revised` 留空）」。

- [ ] **Step 6: 執行測試確認通過（含修改後的 shell 語法檢查）**

```bash
cd ~/CCProject/hermes-training && python3 -m pytest tests/test_extract_scheduled_query_tasks.py -v
bash -n ~/CCProject/hermes-training/run_nightly.sh
```
Expected: pytest 全部 PASS；`bash -n` 沒有輸出（語法正確）

- [ ] **Step 7: Commit**

```bash
cd ~/CCProject
git add hermes-training/extract_scheduled_query_tasks.py hermes-training/tests/test_extract_scheduled_query_tasks.py hermes-training/run_nightly.sh hermes-training/nightly_prompt_template.txt
git commit -m "feat: 把既有排程查詢工作併入 Hermes 夜間訓練集"
```

---

## 完成後的手動確認清單

- [ ] 隔天早上收到 Telegram 早報（不論有沒有可訓練任務）
- [ ] `~/CCProject/hermes-training/logs/<今天日期>.md` 有內容
- [ ] `~/.hermes/learnings.md` 有新增內容（若當晚有差距明顯的任務）
- [ ] `~/.hermes/config.yaml` 的 `agent.personalities.zhtw` 已包含教材區塊
- [ ] 用 `hermes -z "隨便問一句話"` 確認 Hermes 仍正常回應（沒有因為 config 壞掉而啟動失敗）
