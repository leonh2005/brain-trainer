import asyncio
import json
import re

VALID_SECTIONS = {"consensus", "dispute", "frontier"}
AGENT_TIMEOUT = 180.0


class TutorError(Exception):
    """Agent 呼叫或回應解析失敗。"""


async def _call_agent_async(prompt, allow_web=False):
    from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock

    tools = ["Read", "Grep"]
    if allow_web:
        tools += ["WebSearch", "WebFetch"]
    options = ClaudeAgentOptions(allowed_tools=tools, permission_mode="bypassPermissions")
    text = []
    session_id = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text.append(block.text)
        sid = getattr(msg, "session_id", None)
        if sid:
            session_id = sid
    return "".join(text), session_id


def _call_agent(prompt, allow_web=False, session_id=None):
    """與 SDK 的唯一接縫，測試會 monkeypatch 這個函式。"""
    try:
        return asyncio.run(_call_agent_async(prompt, allow_web=allow_web))
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
