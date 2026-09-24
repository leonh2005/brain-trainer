"""jev_filter 分類邏輯測試。用假的 _post 隔離外部 API。"""
import pytest

import jev_filter


def _choices(*picks):
    """組出 Jev 的 answers 格式（每筆一個 choice）。"""
    return {"answers": {f"out{i}": {"choice": p} for i, p in enumerate(picks)}}


@pytest.fixture
def patch_post(monkeypatch):
    """安裝假的 _post，回傳可讀取『最後一次送出的 body』的 dict。"""
    captured = {}

    def _install(response):
        def fake(body, api_key, timeout):
            captured["body"] = body
            captured["api_key"] = api_key
            return response
        monkeypatch.setattr(jev_filter, "_post", fake)
        return captured

    return _install


# ── 既有相關性判斷（特徵測試，重構時保護行為不變）────────────────

def test_classify_marks_relevant(patch_post):
    patch_post(_choices("relevant", "irrelevant"))
    out = jev_filter.classify(["A", "B"], api_key="k")
    assert out[0]["relevant"] is True
    assert out[1]["relevant"] is False


def test_classify_uses_relevance_criteria(patch_post):
    cap = patch_post(_choices("relevant"))
    jev_filter.classify(["A"], api_key="k")
    assert set(cap["body"]["questions"]["out0"]["criteria"]) == {"relevant", "irrelevant"}


def test_classify_falls_back_to_max_probability(patch_post):
    patch_post({"answers": {"out0": {"probabilities": {"relevant": 0.2, "irrelevant": 0.8}}}})
    out = jev_filter.classify(["A"], api_key="k")
    assert out[0]["cls"] == "irrelevant"


# ── 多空三分類（新功能）─────────────────────────────────────────

def test_classify_sentiment_returns_three_classes(patch_post):
    patch_post(_choices("bullish", "bearish", "neutral"))
    out = jev_filter.classify_sentiment(["A", "B", "C"], api_key="k")
    assert [r["sentiment"] for r in out] == ["bullish", "bearish", "neutral"]


def test_classify_sentiment_uses_sentiment_criteria(patch_post):
    cap = patch_post(_choices("bullish"))
    jev_filter.classify_sentiment(["A"], api_key="k")
    criteria = cap["body"]["questions"]["out0"]["criteria"]
    assert set(criteria) == {"bullish", "bearish", "neutral"}


def test_classify_sentiment_falls_back_to_max_probability(patch_post):
    patch_post({"answers": {"out0": {"probabilities": {"bullish": 0.8, "bearish": 0.1, "neutral": 0.1}}}})
    out = jev_filter.classify_sentiment(["A"], api_key="k")
    assert out[0]["sentiment"] == "bullish"


def test_classify_sentiment_empty_input(patch_post):
    patch_post({"answers": {}})
    assert jev_filter.classify_sentiment([], api_key="k") == []


def test_classify_sentiment_raises_on_missing_answers(patch_post):
    patch_post({"unexpected": 1})
    with pytest.raises(jev_filter.JevFilterError):
        jev_filter.classify_sentiment(["A"], api_key="k")


def test_classify_sentiment_all_splits_into_batches(monkeypatch):
    monkeypatch.setattr(jev_filter, "load_api_key", lambda *a, **k: "k")
    batch_sizes = []

    def fake(body, api_key, timeout):
        n = len(body["questions"])
        batch_sizes.append(n)
        return {"answers": {f"out{i}": {"choice": "neutral"} for i in range(n)}}

    monkeypatch.setattr(jev_filter, "_post", fake)
    out = jev_filter.classify_sentiment_all(["t"] * 30, batch_size=25)
    assert len(out) == 30
    assert batch_sizes == [25, 5]


def test_sentiment_score_mapping_is_three_level():
    assert jev_filter.SENTIMENT_SCORE == {"bullish": 10, "bearish": 1, "neutral": 5}
