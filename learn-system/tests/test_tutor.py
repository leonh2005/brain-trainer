import asyncio
import json

import pytest

from learn_system import tutor


def test_generate_map_parses_json(monkeypatch):
    payload = {
        "executable": True,
        "concepts": [
            {"name": "可變 vs 不可變", "section": "consensus", "description": "..."},
            {"name": "GIL", "section": "dispute", "description": "..."},
            {"name": "free-threading", "section": "frontier", "description": "..."},
        ],
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    result = tutor.generate_map("python", ["write", "exam"], verify_sources=False)
    assert result["executable"] is True
    assert len(result["concepts"]) == 3
    assert result["concepts"][0]["section"] == "consensus"


def test_generate_map_strips_markdown_fence(monkeypatch):
    payload = {"executable": True, "concepts": [{"name": "x", "section": "consensus", "description": "d"}]}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (fenced, None))
    assert tutor.generate_map("python", ["write"], False)["concepts"][0]["name"] == "x"


def test_generate_map_rejects_non_json(monkeypatch):
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: ("我覺得 Python 很有趣！", None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_generate_map_rejects_empty_concepts(monkeypatch):
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps({"executable": True, "concepts": []}), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_generate_map_rejects_bad_section(monkeypatch):
    payload = {"executable": True, "concepts": [{"name": "x", "section": "亂寫", "description": "d"}]}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_verify_sources_passes_allow_web(monkeypatch):
    seen = {}
    payload = {"executable": True, "concepts": [{"name": "x", "section": "consensus", "description": "d"}]}

    def fake(prompt, allow_web=False, session_id=None):
        seen["allow_web"] = allow_web
        return json.dumps(payload), None

    monkeypatch.setattr(tutor, "_call_agent", fake)
    tutor.generate_map("python", ["write"], verify_sources=True)
    assert seen["allow_web"] is True


def test_call_agent_forwards_session_id(monkeypatch):
    seen = {}

    async def fake(prompt, allow_web=False, session_id=None):
        seen["session_id"] = session_id
        return "{}", session_id

    monkeypatch.setattr(tutor, "_call_agent_async", fake)
    tutor._call_agent("prompt", session_id="sess-123")
    assert seen["session_id"] == "sess-123"


def test_call_agent_forwards_default_session_id(monkeypatch):
    seen = {}

    async def fake(prompt, allow_web=False, session_id=None):
        seen["session_id"] = session_id
        return "{}", None

    monkeypatch.setattr(tutor, "_call_agent_async", fake)
    tutor._call_agent("prompt")
    assert seen["session_id"] is None


def test_call_agent_times_out_into_tutor_error(monkeypatch):
    """卡住的 agent 必須以 TutorError 收斂，不能永遠等下去。

    SDK 的 query() 本身沒有逾時；逾時若不以例外收斂，app._generate_map 會停在
    domains.status='generating'，前端輪詢不到任何結果，也不會有重試鈕。
    """
    async def slow(prompt, allow_web=False, session_id=None):
        await asyncio.sleep(5)
        return "{}", None

    monkeypatch.setattr(tutor, "_call_agent_async", slow)
    monkeypatch.setattr(tutor, "AGENT_TIMEOUT", 0.05)
    with pytest.raises(tutor.TutorError):
        tutor._call_agent("prompt")


def test_options_resume_carries_session_id():
    assert tutor._build_options(session_id="sess-123").resume == "sess-123"
    assert tutor._build_options().resume is None


def test_options_disable_web_when_allow_web_false():
    opts = tutor._build_options(allow_web=False)
    assert "WebSearch" not in opts.tools
    assert "WebFetch" not in opts.tools
    assert "WebSearch" in opts.disallowed_tools
    assert "WebFetch" in opts.disallowed_tools
    assert "WebSearch" not in opts.allowed_tools
    assert "WebFetch" not in opts.allowed_tools


def test_options_enable_web_when_allow_web_true():
    opts = tutor._build_options(allow_web=True)
    assert "WebSearch" in opts.tools
    assert "WebFetch" in opts.tools
    assert "WebSearch" not in opts.disallowed_tools
    assert "WebFetch" not in opts.disallowed_tools
    assert "WebSearch" in opts.allowed_tools
    assert "WebFetch" in opts.allowed_tools


def test_options_are_a_real_whitelist_with_a_hard_deny_floor():
    for allow_web in (False, True):
        opts = tutor._build_options(allow_web=allow_web)
        # allowed_tools 非限制，故 tools 必須是真正的工具集限制
        assert opts.tools == ["Read", "Grep"] + (["WebSearch", "WebFetch"] if allow_web else [])
        # bypassPermissions 會放行一切，dontAsk 才讓核准清單變成白名單
        assert opts.permission_mode == "dontAsk"
        for tool in ("Bash", "Write", "Edit", "NotebookEdit"):
            assert tool not in opts.tools
            assert tool in opts.disallowed_tools


def test_options_isolate_mcp_servers():
    assert tutor._build_options().strict_mcp_config is True
    assert tutor._build_options(allow_web=True).strict_mcp_config is True


def test_options_pin_the_working_directory():
    # 讀取邊界來自工作目錄；未明設就會隨啟動目錄飄移
    for allow_web in (False, True):
        assert tutor._build_options(allow_web=allow_web).cwd == str(tutor.PROJECT_DIR)


def test_options_enable_restricted_mode():
    # --restricted 才讓工作目錄成為硬邊界，並忽略 user/project/local 設定
    # （後者會以相加的 allow 規則放行專案外讀取）
    for allow_web in (False, True):
        assert tutor._build_options(allow_web=allow_web).extra_args == {"restricted": None}


def test_options_carry_only_auth_settings(monkeypatch, tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"apiKeyHelper": "/bin/helper", "model": "x"}', encoding="utf-8")
    monkeypatch.setattr(tutor, "USER_SETTINGS_PATH", settings_file)
    settings = tutor._build_options().settings
    assert json.loads(settings) == {"apiKeyHelper": "/bin/helper"}


def test_auth_settings_is_none_when_helper_absent(monkeypatch, tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"model": "x"}', encoding="utf-8")
    monkeypatch.setattr(tutor, "USER_SETTINGS_PATH", settings_file)
    assert tutor._auth_settings() is None
    assert tutor._build_options().settings is None


def test_auth_settings_survives_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tutor, "USER_SETTINGS_PATH", tmp_path / "nope.json")
    assert tutor._auth_settings() is None


def test_generate_write_question(monkeypatch):
    payload = {
        "prompt": "寫一個計數器",
        "payload": {"starter_code": "", "test_code": "from solution import counter\nassert counter()() == 1"},
        "reference_answer": "def counter(): ...",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "write", executable=True)
    assert q["payload"]["test_code"].startswith("from solution")


def test_generate_read_question_requires_fixed_code(monkeypatch):
    payload = {"prompt": "找 bug", "payload": {"code_snippet": "x=1"}, "reference_answer": "少了 fixed_code"}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_question("python", "閉包", "說明", "read", executable=True)


def test_generate_read_question_ok_with_fixed_code(monkeypatch):
    payload = {
        "prompt": "找 bug",
        "payload": {"code_snippet": "print(1)", "fixed_code": "print(2)", "bug_description": "值錯了"},
        "reference_answer": "值錯了",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "read", executable=True)
    assert q["payload"]["fixed_code"] == "print(2)"


def test_generate_question_rejects_non_object_json(monkeypatch):
    # 合法 JSON 不等於是物件；純量或陣列直接 .get() 會拋 AttributeError，
    # 端點只攔 TutorError，前端就會拿到 500。
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: ("[1, 2, 3]", None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_question("python", "閉包", "說明", "exam", executable=True)


def test_generate_question_rejects_non_object_payload(monkeypatch):
    payload = {"prompt": "q", "payload": [1, 2], "reference_answer": "a"}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_question("python", "閉包", "說明", "exam", executable=True)


def test_generate_question_treats_non_string_code_field_as_missing(monkeypatch):
    # 非字串的 test_code 若留著，執行驗證會以 TypeError 變成 500
    payload = {"prompt": "q", "payload": {"test_code": ["assert 1 == 1"]}, "reference_answer": "x"}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_question("python", "閉包", "說明", "write", executable=True)


def test_generate_question_treats_non_string_reference_answer_as_empty(monkeypatch):
    payload = {"prompt": "q", "payload": {"starter_code": "x = 1", "test_code": "assert x == 1"},
               "reference_answer": {"code": "x = 1"}}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "write", executable=True)
    assert q["reference_answer"] == ""


def test_generate_question_strips_code_fences(monkeypatch):
    # Agent 習慣把程式碼包在 ``` 裡；留著會讓之後的執行驗證變 SyntaxError
    payload = {
        "prompt": "寫一個回傳 1 的函式",
        "payload": {"starter_code": "", "test_code": "```python\nassert 1 == 1\n```"},
        "reference_answer": "```python\ndef counter():\n    return 1\n```",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "write", executable=True)
    assert q["payload"]["test_code"] == "assert 1 == 1"
    assert q["reference_answer"] == "def counter():\n    return 1"


def test_generate_question_strips_prose_around_fenced_code(monkeypatch):
    payload = {
        "prompt": "找 bug",
        "payload": {"code_snippet": "以下是程式：\n```python\nraise ValueError('bug')\n```\n請找出問題",
                    "fixed_code": "print('ok')", "bug_description": "不該拋錯"},
        "reference_answer": "不該拋錯",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "read", executable=True)
    assert q["payload"]["code_snippet"] == "raise ValueError('bug')"


def test_strip_fence_keeps_every_block(monkeypatch):
    """兩個圍欄區塊要全部保留。

    只留第一個會靜默丟掉斷言：模型給「匯入」與「測試」兩塊時，留下的
    `from solution import add` 會在任何定義得出 add 的答案上都通過。
    """
    two = ("```python\nfrom solution import add\n```\n\n"
           "```python\ndef test_add():\n    assert add(1, 2) == 3\n```")
    stripped = tutor._strip_fence(two)
    assert "from solution import add" in stripped
    assert "assert add(1, 2) == 3" in stripped


def test_strip_fence_ignores_space_separated_language_tag():
    # ` ``` python ` 的 python 曾被留在程式碼裡變成 SyntaxError
    assert tutor._strip_fence("``` python\nassert 1 == 1\n```") == "assert 1 == 1"


def test_strip_fence_keeps_indentation_after_blank_line():
    # 空行後的縮排曾被 \s* 吃掉，讓縮排更深的下一行變成 IndentationError
    text = "```python\n\n    def f():\n        pass\n```"
    assert tutor._strip_fence(text) == "def f():\n    pass"


def test_strip_fence_handles_single_line_fence_without_tag():
    # 無標籤的單行圍欄：開頭的 x 曾被當成語言標籤吃掉
    assert tutor._strip_fence("```x = 1```") == "x = 1"


def test_extract_json_accepts_literal_newline_in_string():
    """字串值裡的字面換行必須能解析。

    `json.loads` 預設 strict=True 會以 "Invalid control character" 拒絕，但模型
    輸出多行 test_code 時就是這樣寫的——write 題因此整批 502，map/explain
    因為沒有程式碼欄位所以踩不到。
    """
    text = '{"test_code":"assert x\n\nassert y"}'
    assert tutor._extract_json(text) == {"test_code": "assert x\n\nassert y"}


def test_extract_json_accepts_literal_newline_inside_fence():
    text = '```json\n{"test_code":"assert x\n\nassert y"}\n```'
    assert tutor._extract_json(text)["test_code"] == "assert x\n\nassert y"


def test_generate_write_question_with_multiline_test_code(monkeypatch):
    """真實失敗情境：模型直接吐多行 test_code（字面換行，非跳脫）。"""
    raw = ('```json\n{"prompt":"寫一個計數器","payload":{'
           '"starter_code":"def counter():\n    raise NotImplementedError",'
           '"test_code":"from solution import counter\n\n\ndef test_it():\n    assert counter()() == 1"},'
           '"reference_answer":"def counter():\n    return lambda: 1"}\n```')
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (raw, None))
    q = tutor.generate_question("python", "閉包", "說明", "write", executable=True)
    assert q["prompt"] == "寫一個計數器"
    assert "assert counter()() == 1" in q["payload"]["test_code"]


def test_extract_json_handles_fence_inside_a_string_value():
    """字串值自己帶 ``` 時，非貪婪的圍欄 regex 會先配對到那個假圍欄而截斷。"""
    text = '```json\n{"code_snippet":"print(1) ```x``` ok"}\n```'
    assert tutor._extract_json(text) == {"code_snippet": "print(1) ```x``` ok"}


def test_extract_json_falls_back_to_the_original_text():
    """可解析的 JSON 在第一個圍欄之外時，仍要回頭掃原文。

    只在圍欄取出的內容裡找大括號，會讓「原文其實可解析」的情況整個漏掉。
    """
    text = "前言 ```code``` {\"a\": 1}"
    assert tutor._extract_json(text) == {"a": 1}


def test_question_prompt_example_is_not_indented():
    """提示詞裡的範例必須從第 0 欄開始。

    範例若跟著 bullet 的縮排，模型照抄又漏了圍欄時，`_strip_fence` 只會做
    strip（dedent 只在有圍欄時走），內部那兩格縮排會留著 → SyntaxError →
    「1 error」→ 題目在建立時被 422，整個 write 題型都出不了題。
    """
    prompt = tutor.QUESTION_PROMPT
    assert "\nfrom solution import add\n" in prompt
    assert "\ndef test_add():\n" in prompt


def test_map_prompt_asks_for_python_only_executability():
    """executable 的語意只能是「這個領域的動手實作就是寫 Python」。

    提示詞若問成「可否由程式碼執行來客觀驗證」，JavaScript 這類領域會拿到
    executable=true，接著每一題 write 都在 _validate_question 被 422 擋掉
    （執行器與 QUESTION_PROMPT 都只認 Python/pytest），永遠出不了題。
    """
    prompt = tutor.MAP_PROMPT
    assert "Python" in prompt
    assert "JavaScript" in prompt
    assert "是否可以由程式碼執行來客觀驗證" not in prompt
