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
