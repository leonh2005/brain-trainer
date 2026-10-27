# -*- coding: utf-8 -*-
"""app.py 純邏輯 + Flask 路由測試。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import PARAMS, _parse_json, app, lookup_principles

# ── lookup_principles ────────────────────────────────────────────


def test_lookup_principles_returns_list_for_known_pair():
    """已知存在的矛盾組合要回傳非空的原理 ID 列表。"""
    result = lookup_principles(1, 3)
    assert isinstance(result, list)
    assert result == [15, 8, 29, 34]


def test_lookup_principles_returns_empty_for_missing_pair():
    """矩陣內不存在的組合要回傳空 list，不拋例外。"""
    assert lookup_principles(1, 2) == []


def test_lookup_principles_returns_empty_when_same_parameter():
    """改善與惡化參數相同時，根據 app.py:42 必須回傳空 list。"""
    assert lookup_principles(5, 5) == []


def test_lookup_principles_out_of_range_returns_empty():
    """超出 1-39 範圍的參數要回傳空 list（MATRIX.get 找不到）。"""
    assert lookup_principles(0, 1) == []
    assert lookup_principles(100, 1) == []


# ── _parse_json ──────────────────────────────────────────────────


def test_parse_json_plain_object():
    """純 JSON 字串要能直接 parse。"""
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_strips_markdown_code_fence():
    """LLM 常回的 ```json ... ``` 包版要能正確去除。"""
    text = '```json\n{"improving": 1, "worsening": 2}\n```'
    assert _parse_json(text) == {"improving": 1, "worsening": 2}


def test_parse_json_strips_bare_code_fence():
    """沒標語言的 ``` 也要能去除。"""
    text = '```\n{"x": 42}\n```'
    assert _parse_json(text) == {"x": 42}


def test_parse_json_strips_surrounding_backticks():
    """單一個 ` 包起來也要能處理。"""
    text = '`{"x": 1}`'
    assert _parse_json(text) == {"x": 1}


def test_parse_json_invalid_raises():
    """完全不是 JSON 的字串要拋 json.JSONDecodeError。"""
    import json as _json
    with pytest.raises(_json.JSONDecodeError):
        _parse_json("not json at all")


# ── Flask routes: input validation ───────────────────────────────


@pytest.fixture
def client():
    """Flask test client，不啟動真實 server。"""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_lookup_missing_params_returns_400(client):
    """沒帶 improving/worsening 要回 400。"""
    resp = client.get("/api/lookup")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_api_lookup_only_improving_returns_400(client):
    """只帶一個參數要回 400。"""
    resp = client.get("/api/lookup?improving=1")
    assert resp.status_code == 400


def test_api_lookup_valid_pair_returns_principle_ids(client):
    """合法組合要回傳 principle_ids list。"""
    resp = client.get("/api/lookup?improving=1&worsening=3")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["principle_ids"] == [15, 8, 29, 34]


def test_api_lookup_same_param_returns_empty_list(client):
    """相同參數要回空 list（不报错）。"""
    resp = client.get("/api/lookup?improving=5&worsening=5")
    assert resp.status_code == 200
    assert resp.get_json()["principle_ids"] == []


def test_api_data_returns_all_params_and_principles(client):
    """/api/data 要回傳完整的 39 參數與 40 原理。"""
    resp = client.get("/api/data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["params"]) == len(PARAMS)
    assert len(data["principles"]) == 40


def test_api_analyze_empty_body_returns_400(client):
    """problem 空白要回 400，不呼叫 Groq。"""
    resp = client.post("/api/analyze", json={"problem": "   "})
    assert resp.status_code == 400


def test_api_analyze_missing_json_returns_400(client):
    """送空 JSON（無 problem）要回 400。"""
    resp = client.post("/api/analyze", json={})
    assert resp.status_code == 400


def test_api_analyze_without_content_type_returns_415(client):
    """完全沒送 body／Content-Type 時，Flask 的 request.json 會先回 415。

    這是 Flask 的既定行為（415 Unsupported Media Type 比 400 更精確），
    不是本服務自行判斷的錯誤，因此獨立成一個測試記錄下來。
    """
    resp = client.post("/api/analyze")
    assert resp.status_code == 415


def test_api_suggest_missing_params_returns_400(client):
    """缺 improving/worsening 要回 400。"""
    resp = client.post("/api/suggest", json={"problem": "x"})
    assert resp.status_code == 400


# ── analyze_problem: Groq mock ───────────────────────────────────


def test_analyze_problem_parses_groq_response(monkeypatch):
    """Groq 回合法 JSON 時要解析出 improving/worsening/reasoning。"""
    import app as app_module
    from app import analyze_problem

    class FakeMsg:
        content = '{"improving": 5, "worsening": 10, "reasoning": "測試理由"}'

    class FakeChoice:
        message = FakeMsg()
        finish_reason = "stop"

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    return FakeResp()
            completions = Completions()
        chat = Chat()

    monkeypatch.setattr(app_module, "groq_client", lambda: FakeClient())
    result = analyze_problem("測試問題")
    assert result == {"improving": 5, "worsening": 10, "reasoning": "測試理由"}


def test_analyze_problem_rejects_same_params(monkeypatch):
    """Groq 回 improving==worsening 時要拋 ValueError。"""
    import app as app_module
    from app import analyze_problem

    class FakeMsg:
        content = '{"improving": 7, "worsening": 7, "reasoning": "bad"}'

    class FakeChoice:
        message = FakeMsg()
        finish_reason = "stop"

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    return FakeResp()
            completions = Completions()
        chat = Chat()

    monkeypatch.setattr(app_module, "groq_client", lambda: FakeClient())
    with pytest.raises(ValueError, match="超出範圍或相同"):
        analyze_problem("測試")


def test_analyze_problem_rejects_out_of_range(monkeypatch):
    """Groq 回超出 1-39 範圍時要拋 ValueError。"""
    import app as app_module
    from app import analyze_problem

    class FakeMsg:
        content = '{"improving": 100, "worsening": 1, "reasoning": "bad"}'

    class FakeChoice:
        message = FakeMsg()
        finish_reason = "stop"

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    return FakeResp()
            completions = Completions()
        chat = Chat()

    monkeypatch.setattr(app_module, "groq_client", lambda: FakeClient())
    with pytest.raises(ValueError, match="超出範圍或相同"):
        analyze_problem("測試")


def test_analyze_problem_raises_on_empty_content(monkeypatch):
    """Groq 回傳空內容時要拋 RuntimeError。"""
    import app as app_module
    from app import analyze_problem

    class FakeMsg:
        content = None

    class FakeChoice:
        message = FakeMsg()
        finish_reason = "length"

    class FakeResp:
        choices = [FakeChoice()]

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    return FakeResp()
            completions = Completions()
        chat = Chat()

    monkeypatch.setattr(app_module, "groq_client", lambda: FakeClient())
    with pytest.raises(RuntimeError, match="沒有回傳內容"):
        analyze_problem("測試")
