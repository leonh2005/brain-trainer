"""AI 聊天層 — Claude Agent SDK，唯讀工具 + Bash 白名單"""
import json
import os

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
    PermissionResultAllow,
    PermissionResultDeny,
)

CC = '/Users/steven/CCProject'
MEMORY_DIR = '/Users/steven/.claude/projects/-Users-steven-CCProject/memory'

# Bash 只允許這些開頭的命令（唯讀查看）
BASH_ALLOWED_PREFIXES = (
    'tail', 'head', 'cat', 'ls', 'grep', 'rg', 'wc', 'stat', 'du',
    'launchctl list', 'launchctl print',
)

# 這些前綴需額外檢查（有破壞性參數）
BASH_DANGER_FLAGS = ('-delete', '-exec', '-o ', '-O', '-X ', '--output', '--remove')


def _bash_ok(cmd: str) -> bool:
    # find 允許但拒絕破壞性參數
    if cmd.startswith('find'):
        return not any(f in cmd for f in ('-delete', '-exec', '-fprint'))
    # sqlite3 僅允許唯讀模式開啟
    if cmd.startswith('sqlite3'):
        return 'mode=ro' in cmd or '-readonly' in cmd
    # curl 僅允許查本機服務、且不得寫檔或改方法
    if cmd.startswith('curl http://localhost:'):
        return not any(f in cmd for f in ('-o', '-O', '-X', '--output', '--upload'))
    # crontab 只允許純列出
    if cmd.startswith('crontab'):
        return cmd.strip() == 'crontab -l'
    return cmd.startswith(BASH_ALLOWED_PREFIXES)


async def _guard(tool_name: str, tool_input: dict, context):
    if tool_name in ('Write', 'Edit', 'NotebookEdit'):
        return PermissionResultDeny(behavior='deny', message='指揮中心聊天為唯讀模式，不允許修改檔案')
    if tool_name == 'Bash':
        cmd = str(tool_input.get('command', '')).strip()
        # 禁複合命令與重定向：prefix 檢查只對單一命令有意義
        if any(ch in cmd for ch in (';', '&', '|', '`', '$(', '>', '\n')):
            return PermissionResultDeny(
                behavior='deny',
                message='不允許複合命令、管線或重定向，請一次執行一個唯讀命令')
        if not _bash_ok(cmd):
            return PermissionResultDeny(
                behavior='deny',
                message=f'Bash 白名單外或含破壞性參數：{cmd[:80]}（只允許唯讀查看類命令）')
    return PermissionResultAllow(behavior='allow')


def _system_prompt() -> str:
    parts = [
        '你是 Steven 的 AI 指揮中心助理。永遠用繁體中文回答，簡潔直接。',
        '你可以讀取 ~/CCProject 下所有服務的資料（log、SQLite、JSON 快取）'
        '與本機服務 API（curl http://localhost:PORT）。你是唯讀的：不得修改任何檔案。',
    ]
    try:
        with open(f'{CC}/command-center/README.md') as f:
            parts.append('## 服務與資料源總覽\n' + f.read())
    except OSError:
        pass
    try:
        with open(f'{MEMORY_DIR}/MEMORY.md') as f:
            parts.append(
                '## 記憶索引（各檔案在 ' + MEMORY_DIR + ' 下，需要背景時用 Read 讀取對應檔案）\n'
                + f.read())
    except OSError:
        pass
    return '\n\n'.join(parts)


async def chat_stream(prompt: str):
    """SSE generator：逐字回覆 + 最後一則 usage 統計"""
    options = ClaudeAgentOptions(
        system_prompt=_system_prompt(),
        allowed_tools=['Read', 'Grep', 'Glob', 'Bash'],
        can_use_tool=_guard,
        cwd=CC,
        max_turns=15,
    )
    async def _prompt_stream():
        # can_use_tool 要求 streaming input mode，故以 AsyncIterable 形式提供 prompt
        yield {'type': 'user', 'message': {'role': 'user', 'content': prompt}}

    try:
        async for message in query(prompt=_prompt_stream(), options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield f'data: {json.dumps({"type": "text", "text": block.text}, ensure_ascii=False)}\n\n'
            elif isinstance(message, ResultMessage):
                usage = {
                    'type': 'result',
                    'cost_usd': message.total_cost_usd,
                    'duration_ms': message.duration_ms,
                    'num_turns': message.num_turns,
                    'tokens': (message.usage or {}).get('output_tokens'),
                    'is_error': message.is_error,
                }
                yield f'data: {json.dumps(usage, ensure_ascii=False)}\n\n'
    except Exception as e:
        yield f'data: {json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)}\n\n'
    yield 'data: [DONE]\n\n'
