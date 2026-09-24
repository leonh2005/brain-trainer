"""analyze_pending 兩階段管線測試。Jev 呼叫全部以假函式取代。"""
import pytest

import analyze_pending
import jev_filter
from storage import init_db, save_articles, get_articles


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    save_articles([
        {"source": "ptt", "title": "台積電大漲", "url": "https://ptt.cc/a", "content": "", "published_at": None},
        {"source": "ptt", "title": "秋季家居博覽登場", "url": "https://ptt.cc/b", "content": "", "published_at": None},
        {"source": "ptt", "title": "台股平盤震盪", "url": "https://ptt.cc/c", "content": "", "published_at": None},
    ], path)
    return path


@pytest.fixture
def no_sleep(monkeypatch):
    """重試測試不真的睡。"""
    monkeypatch.setattr(analyze_pending.time, "sleep", lambda s: None)


def _fake_relevance(picks):
    """picks: {標題: 'relevant'/'irrelevant'}"""
    def fn(titles, **kwargs):
        return [{"cls": picks[t], "relevant": picks[t] == "relevant", "probs": {}} for t in titles]
    return fn


def _fake_sentiment(picks):
    """picks: {標題: 'bullish'/'bearish'/'neutral'}"""
    def fn(titles, **kwargs):
        return [{"sentiment": picks[t], "probs": {}} for t in titles]
    return fn


ALL_RELEVANT = {"台積電大漲": "relevant", "秋季家居博覽登場": "relevant", "台股平盤震盪": "relevant"}


def test_relevant_articles_get_mapped_scores(monkeypatch, db_path):
    monkeypatch.setattr(jev_filter, "classify_all", _fake_relevance(ALL_RELEVANT))
    monkeypatch.setattr(jev_filter, "classify_sentiment_all", _fake_sentiment({
        "台積電大漲": "bullish", "秋季家居博覽登場": "bearish", "台股平盤震盪": "neutral"}))

    analyze_pending.run(days=1, limit=None, dry=False, db_path=db_path)

    scores = {a["title"]: a["score"] for a in get_articles(db_path=db_path)["articles"]}
    assert scores["台積電大漲"] == 10
    assert scores["秋季家居博覽登場"] == 1
    assert scores["台股平盤震盪"] == 5


def test_irrelevant_articles_are_marked_and_not_scored_by_sentiment(monkeypatch, db_path):
    monkeypatch.setattr(jev_filter, "classify_all", _fake_relevance({
        "台積電大漲": "relevant", "秋季家居博覽登場": "irrelevant", "台股平盤震盪": "relevant"}))

    sent_titles = []

    def spy_sentiment(titles, **kwargs):
        sent_titles.extend(titles)
        return [{"sentiment": "neutral", "probs": {}} for _ in titles]

    monkeypatch.setattr(jev_filter, "classify_sentiment_all", spy_sentiment)

    analyze_pending.run(days=1, limit=None, dry=False, db_path=db_path)

    # 不相關的不該送進多空判斷
    assert "秋季家居博覽登場" not in sent_titles
    assert set(sent_titles) == {"台積電大漲", "台股平盤震盪"}

    arts = {a["title"]: a for a in get_articles(db_path=db_path)["articles"]}
    assert arts["秋季家居博覽登場"]["auto_irrelevant"] == 1
    assert arts["台積電大漲"]["auto_irrelevant"] == 0


def test_relevance_failure_retries_then_succeeds(monkeypatch, db_path, no_sleep):
    calls = {"n": 0}

    def flaky(titles, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise jev_filter.JevFilterError("529 過載")
        return [{"cls": "relevant", "relevant": True, "probs": {}} for _ in titles]

    monkeypatch.setattr(jev_filter, "classify_all", flaky)
    monkeypatch.setattr(jev_filter, "classify_sentiment_all",
                        _fake_sentiment({t: "neutral" for t in ALL_RELEVANT}))

    stats = analyze_pending.run(days=1, limit=None, dry=False, db_path=db_path)

    assert calls["n"] == 2
    assert stats["jev_relevant"] == 3


def test_sentiment_failure_retries_then_succeeds(monkeypatch, db_path, no_sleep):
    monkeypatch.setattr(jev_filter, "classify_all", _fake_relevance(ALL_RELEVANT))
    calls = {"n": 0}

    def flaky(titles, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise jev_filter.JevFilterError("529 過載")
        return [{"sentiment": "bullish", "probs": {}} for _ in titles]

    monkeypatch.setattr(jev_filter, "classify_sentiment_all", flaky)

    stats = analyze_pending.run(days=1, limit=None, dry=False, db_path=db_path)

    assert calls["n"] == 2
    assert stats["sentiment_done"] == 3
    assert {a["score"] for a in get_articles(db_path=db_path)["articles"]} == {10}


def test_sentiment_gives_up_after_max_attempts(monkeypatch, db_path, no_sleep):
    monkeypatch.setattr(jev_filter, "classify_all", _fake_relevance(ALL_RELEVANT))

    def always_fail(titles, **kwargs):
        raise jev_filter.JevFilterError("529 過載")

    monkeypatch.setattr(jev_filter, "classify_sentiment_all", always_fail)

    stats = analyze_pending.run(days=1, limit=None, dry=False, db_path=db_path)

    # 放棄後不該留下半套資料
    assert stats["sentiment_done"] == 0
    assert all(a["score"] is None for a in get_articles(db_path=db_path)["articles"])
