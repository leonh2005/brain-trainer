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


def test_options_resume_carries_session_id():
    assert tutor._build_options(session_id="sess-123").resume == "sess-123"
    assert tutor._build_options().resume is None


def test_options_disable_web_when_allow_web_false():
    opts = tutor._build_options(allow_web=False)
    assert "WebSearch" not in opts.tools
    assert "WebFetch" not in opts.tools
    assert "WebSearch" in opts.disallowed_tools
    assert "WebFetch" in opts.disallowed_tools
    assert not any("WebSearch" in rule for rule in opts.allowed_tools)
    assert not any("WebFetch" in rule for rule in opts.allowed_tools)


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


def test_options_scope_reads_and_greps_to_the_project():
    scope = f"//{tutor.PROJECT_DIR}/**"
    for allow_web in (False, True):
        allowed = tutor._build_options(allow_web=allow_web).allowed_tools
        assert f"Read({scope})" in allowed
        assert f"Grep({scope})" in allowed
        # 不可有無範圍的整支工具規則，否則等於全機可讀
        assert "Read" not in allowed
        assert "Grep" not in allowed
