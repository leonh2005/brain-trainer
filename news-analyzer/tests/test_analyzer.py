import json
from unittest.mock import patch, MagicMock
from analyzer import analyze_batch, parse_groq_response

SAMPLE_ARTICLES = [
    {"id": 1, "source": "ptt", "title": "台積電大漲 5%", "content": "外資買超，法說會優於預期"},
    {"id": 2, "source": "cnyes", "title": "聯準會升息恐慌", "content": "市場擔憂通膨持續"},
]

SAMPLE_GROQ_RESPONSE = json.dumps([
    {"id": 1, "score": 8, "summary": "台積電法說優於預期，外資買超", "tags": ["台積電", "外資", "法說會"]},
    {"id": 2, "score": 3, "summary": "Fed 升息恐慌壓制市場", "tags": ["Fed", "升息", "通膨"]},
])


def test_parse_groq_response_valid():
    result = parse_groq_response(SAMPLE_GROQ_RESPONSE, SAMPLE_ARTICLES)
    assert len(result) == 2
    assert result[0]["score"] == 8
    assert result[0]["summary"] == "台積電法說優於預期，外資買超"
    assert result[0]["tags"] == ["台積電", "外資", "法說會"]


def test_parse_groq_response_invalid_json():
    result = parse_groq_response("not json", SAMPLE_ARTICLES)
    assert result == []


def test_analyze_batch_calls_groq():
    mock_message = MagicMock()
    mock_message.content = SAMPLE_GROQ_RESPONSE

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion

    with patch("analyzer.build_groq_client", return_value=mock_client):
        result = analyze_batch(SAMPLE_ARTICLES)

    assert len(result) == 2
    assert result[0]["score"] in range(1, 11)


def test_analyze_batch_handles_groq_failure():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("rate limit")

    with patch("analyzer.build_groq_client", return_value=mock_client):
        result = analyze_batch(SAMPLE_ARTICLES)

    assert result == []
