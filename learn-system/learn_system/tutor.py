import asyncio
import json
import re
from pathlib import Path

VALID_SECTIONS = {"consensus", "dispute", "frontier"}
AGENT_TIMEOUT = 180.0

PROJECT_DIR = Path(__file__).resolve().parent.parent
READ_TOOLS = ["Read", "Grep"]
WEB_TOOLS = ["WebSearch", "WebFetch"]
BLOCKED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit"]


class TutorError(Exception):
    """Agent 呼叫或回應解析失敗。"""


def _build_options(allow_web=False, session_id=None):
    """建立本模組唯一的能力邊界。

    幾個都必須同時成立才擋得住，任一單獨用都是名存實亡：

    - `allowed_tools` 只是「免詢問核准清單」不是限制；`tools` 才是限定可用
      基礎工具集的地方。
    - `bypassPermissions` 會在諮詢任何 callback 前放行所有工具，故改用
      `dontAsk`（未預先核准者一律拒絕），核准清單才成為真正的白名單。
    - `strict_mcp_config=True` 擋掉使用者/專案/外掛設定帶進來的 MCP 伺服器
      （否則全機的 playwright／context7 等工具會整批注入，等同網路與 RCE）。
    - 讀取與搜尋以 `Read(//<專案>/**)`、`Grep(//<專案>/**)` 規則限定在專案內，
      避免注入的 prompt 讀到 `~/.ssh`、`.secrets` 等專案外檔案。

    `allow_web=False` 時網路工具既不在 tools 也被列入 disallowed，完全不可達。
    `setting_sources` 不可設為 `[]`（SDK 隔離模式）——那會連 apiKeyHelper
    也一併停用而使身分驗證失敗。
    """
    from claude_agent_sdk import ClaudeAgentOptions

    tools = list(READ_TOOLS)
    if allow_web:
        tools += WEB_TOOLS

    scope = f"//{PROJECT_DIR}/**"
    allowed = [f"Read({scope})", f"Grep({scope})"]
    if allow_web:
        allowed += WEB_TOOLS

    blocked = list(BLOCKED_TOOLS)
    if not allow_web:
        blocked += WEB_TOOLS

    return ClaudeAgentOptions(
        tools=tools,
        allowed_tools=allowed,
        disallowed_tools=blocked,
        permission_mode="dontAsk",
        strict_mcp_config=True,
        resume=session_id,
    )


async def _call_agent_async(prompt, allow_web=False, session_id=None):
    from claude_agent_sdk import query, AssistantMessage, TextBlock

    options = _build_options(allow_web=allow_web, session_id=session_id)
    text = []
    observed_session = session_id
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text.append(block.text)
        sid = getattr(msg, "session_id", None)
        if sid:
            observed_session = sid
    return "".join(text), observed_session


def _call_agent(prompt, allow_web=False, session_id=None):
    """與 SDK 的唯一接縫，測試會 monkeypatch 這個函式。"""
    try:
        return asyncio.run(_call_agent_async(prompt, allow_web=allow_web, session_id=session_id))
    except Exception as e:
        raise TutorError(f"Agent 呼叫失敗：{e}") from e


def _extract_json(text):
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate.strip())
    except json.JSONDecodeError:
        # 嘗試抓出第一個完整物件
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise TutorError("Agent 回應不是合法 JSON")


MAP_PROMPT = """你是一位嚴謹的學科導師，專長是替學習者建立「智識地圖」。

學習領域：{domain}
學習者的目標：{goals}

請用該領域專家的視角，產出三類內容：
1. consensus（共識）：該領域專家普遍共享的 5 個核心思維模型。這是學習者必須先建立的底層框架。
2. dispute（分歧）：專家之間最激烈的 3 個爭議點。這標示出「已確定基礎」與「高價值探索區」的分界。
3. frontier（探索區）：2 個尚未有定論的前沿問題。

另外判斷一件事：這個領域的「動手實作」是否可以由程式碼執行來客觀驗證（例如程式語言可以，經濟學不行）。

只回傳 JSON，不要任何其他文字：
{{
  "executable": true 或 false,
  "concepts": [
    {{"name": "概念名稱（精簡，不超過 12 字）", "section": "consensus|dispute|frontier", "description": "一到兩句說明"}}
  ]
}}"""


def generate_map(domain_name, goals, verify_sources):
    prompt = MAP_PROMPT.format(domain=domain_name, goals="、".join(goals))
    if verify_sources:
        prompt += "\n\n請先查證權威來源（官方文件、經典教材）再作答，並在必要時修正你的敘述。"
    text, _ = _call_agent(prompt, allow_web=verify_sources)
    data = _extract_json(text)

    concepts = data.get("concepts") or []
    if not concepts:
        raise TutorError("Agent 未產出任何概念")
    for c in concepts:
        if c.get("section") not in VALID_SECTIONS:
            raise TutorError(f"未知的 section：{c.get('section')}")
        if not c.get("name"):
            raise TutorError("概念缺少名稱")

    return {"executable": bool(data.get("executable", True)), "concepts": concepts}
