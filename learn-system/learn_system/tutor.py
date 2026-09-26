import asyncio
import json
import re
from pathlib import Path

VALID_SECTIONS = {"consensus", "dispute", "frontier"}
AGENT_TIMEOUT = 180.0

PROJECT_DIR = Path(__file__).resolve().parent.parent
USER_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
READ_TOOLS = ["Read", "Grep"]
WEB_TOOLS = ["WebSearch", "WebFetch"]
BLOCKED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit"]


class TutorError(Exception):
    """Agent 呼叫或回應解析失敗。"""


def _auth_settings():
    """只帶回身分驗證設定，供 `--restricted` 使用。

    `--restricted` 會忽略 user/project/local 設定檔（專案 settings.local.json
    裡 38 條 allow 規則，含 `Read(//Users/steven/youtube-monitor/**)`，就是靠
    這個關掉的），但連 `~/.claude/settings.json` 的 apiKeyHelper 一起停用會導致
    "Not logged in"。`--settings` 在 restricted 模式下仍會生效，故以它單獨注入。
    """
    try:
        data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    helper = data.get("apiKeyHelper")
    return json.dumps({"apiKeyHelper": helper}) if helper else None


def _build_options(allow_web=False, session_id=None):
    """建立本模組唯一的能力邊界。每一條都必須真的會綁，任一單獨用都名存實亡：

    - `tools` 才是限定可用工具集的地方；`allowed_tools` 只是「免詢問核准清單」，
      本身不限制任何東西。
    - `permission_mode="dontAsk"` 讓核准清單成為真正的白名單；`bypassPermissions`
      會在諮詢任何 callback 前放行一切。
    - `disallowed_tools` 為硬性封鎖下限，不依賴上述兩者被正確理解。
    - `strict_mcp_config=True` 擋掉使用者/專案/外掛設定帶進來的 MCP 伺服器
      （否則本機的 playwright／context7 等 84 項工具會整批注入，等同網路與 RCE）。
    - `cwd=PROJECT_DIR` + `--restricted` 把檔案工具限制在工作目錄內。**cwd 必須
      明設**：不設時子行程沿用啟動時的目錄，邊界會隨「從哪裡啟動」飄移。
    - `--restricted` 同時忽略 user/project/local 設定檔——這是必要的，因為
      allow 規則與 `--allowedTools` 是**相加**的，專案 settings.local.json 的
      allow 能逕行放行專案外的讀取。
    - `--settings` 只帶 apiKeyHelper（見 `_auth_settings`）。

    注意（實測）：把 `Read(//<專案>/**)` 這類路徑限縮規則放進 `allowed_tools`
    是**無效的**——真正的讀取邊界來自工作目錄而非該規則，故不採用。
    `setting_sources=[]`（SDK 隔離模式）與 `--restricted` 一樣會停用
    apiKeyHelper，差別是前者沒有補救途徑，直接 "Not logged in"。

    `allow_web=False` 時網路工具既不在 tools、也被列入 disallowed；`--restricted`
    同樣會移除 WebFetch，除非 tools 指明——兩者一致。
    """
    from claude_agent_sdk import ClaudeAgentOptions

    tools = list(READ_TOOLS)
    if allow_web:
        tools += WEB_TOOLS

    blocked = list(BLOCKED_TOOLS)
    if not allow_web:
        blocked += WEB_TOOLS

    return ClaudeAgentOptions(
        tools=tools,
        allowed_tools=tools,
        disallowed_tools=blocked,
        permission_mode="dontAsk",
        strict_mcp_config=True,
        cwd=str(PROJECT_DIR),
        settings=_auth_settings(),
        extra_args={"restricted": None},
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


QUESTION_PROMPT = """你是嚴謹的學科導師，要替學習者出一題來檢驗他是否「真正理解」而非死記。

學習領域：{domain}
正在練的概念：{concept}
概念說明：{description}
題型：{goal_type}

題型定義：
- write：給規格讓學習者寫出程式。payload 需含 {{"starter_code": "...", "test_code": "..."}}，
  test_code 是會以 `from solution import ...` 匯入學習者程式碼的 pytest 斷言。
- read：給一段有 bug 的程式讓學習者找出問題並說明。payload 需含
  {{"code_snippet": "有 bug 的版本", "fixed_code": "修正後版本", "bug_description": "標準答案"}}。
  code_snippet 必須真的會出錯或輸出錯誤結果，fixed_code 必須真的能修正它。
- principle：給一段程式讓學習者預測輸出並解釋原因。payload 需含 {{"code_snippet": "..."}}。
- exam：模擬檢定考題。payload 為 {{}}。

只回傳 JSON，不要任何其他文字：
{{"prompt": "題目敘述", "payload": {{...}}, "reference_answer": "標準答案或參考解法"}}"""


def generate_question(domain_name, concept_name, concept_description, goal_type, executable):
    prompt = QUESTION_PROMPT.format(
        domain=domain_name, concept=concept_name,
        description=concept_description or "（尚未生成）", goal_type=goal_type,
    )
    text, _ = _call_agent(prompt)
    data = _extract_json(text)

    payload = data.get("payload") or {}
    if not data.get("prompt"):
        raise TutorError("題目缺少敘述")
    if goal_type == "write" and executable and not payload.get("test_code"):
        raise TutorError("write 題缺少 test_code")
    if goal_type == "read" and executable:
        if not payload.get("code_snippet") or not payload.get("fixed_code"):
            raise TutorError("read 題缺少 code_snippet 或 fixed_code")
    if goal_type == "principle" and executable and not payload.get("code_snippet"):
        raise TutorError("principle 題缺少 code_snippet")

    return {"prompt": data["prompt"], "payload": payload,
            "reference_answer": data.get("reference_answer", "")}
