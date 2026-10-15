# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import research


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _patch_groq(monkeypatch, content: str):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(research.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_groq_key_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert research._groq_key() == "gsk_test"


def test_extract_script_rejects_empty_news():
    with pytest.raises(research.ResearchError):
        research.extract_script([])


def test_extract_script_sends_news_and_parses_json(monkeypatch):
    captured = _patch_groq(monkeypatch, '{"label": "x", "targets": []}')
    news = [{"title": "杜金龍看好台股", "link": "u", "published": "2026-09-24",
             "summary": "上看53000"}]
    out = research.extract_script(news)
    assert out == {"label": "x", "targets": []}
    prompt = captured["body"]["messages"][1]["content"]
    assert "上看53000" in prompt and "杜金龍看好台股" in prompt
    assert captured["body"]["model"] == research.GROQ_MODEL


def test_extract_script_empty_content_raises(monkeypatch):
    _patch_groq(monkeypatch, "")
    with pytest.raises(research.ResearchError):
        research.extract_script([{"title": "t", "link": "", "published": "", "summary": ""}])


def test_extract_script_non_json_raises(monkeypatch):
    _patch_groq(monkeypatch, "這不是 JSON")
    with pytest.raises(research.ResearchError):
        research.extract_script([{"title": "t", "link": "", "published": "", "summary": ""}])
