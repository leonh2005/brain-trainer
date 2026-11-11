"""filters 的單元測試：幻覺過濾與段落合併。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import filters  # noqa: E402


def test_junk_drops_known_hallucinations():
    for text in ["中文字幕志愿者 李宗盛", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
                 "there", "!!!!!!!!", ""]:
        assert filters.is_junk(text), text


def test_junk_drops_repeated_phrases():
    assert filters.is_junk("超久超久超久超久超久超久")
    assert filters.is_junk("你脑袋你脑袋你脑袋你脑袋你脑袋")


def test_junk_keeps_real_sentences():
    for text in ["我想要找機械表", "那大概要排多久啊", "對對對", "生日禮物嗎?"]:
        assert not filters.is_junk(text), text


def test_is_repetitive_only_for_pure_loops():
    assert filters.is_repetitive("超久的超久的超久的")
    assert not filters.is_repetitive("我想要找機械表那面盤的話倒是沒有限定")


def test_clean_segments_skips_leading_noise():
    segs = [
        {"start": 0, "end": 2, "text": "超久的超久的超久的"},
        {"start": 2, "end": 4, "text": "there"},
        {"start": 4, "end": 6, "text": "好"},
        {"start": 6, "end": 8, "text": "!!!!"},
        {"start": 400, "end": 403, "text": "你們有這個系列的標準嗎"},
        {"start": 403, "end": 406, "text": "我想要找機械表"},
        {"start": 406, "end": 409, "text": "那面盤的話倒是沒有限定"},
        {"start": 409, "end": 412, "text": "目前現場只有這兩款"},
        {"start": 412, "end": 415, "text": "都是三眼的"},
    ]
    out = filters.clean_segments(segs)
    assert out[0]["start"] == 400, "音樂段的幻覺要整段丟掉"


def test_clean_segments_keeps_early_speech():
    """開頭就是對話時不能因為前幾句短就把真實內容刪掉。"""
    segs = [
        {"start": 0, "end": 2, "text": "你好"},
        {"start": 2, "end": 5, "text": "我想要找機械表"},
        {"start": 5, "end": 8, "text": "那大概要排多久啊"},
        {"start": 8, "end": 10, "text": "對對對"},
        {"start": 10, "end": 13, "text": "生日禮物嗎"},
        {"start": 13, "end": 16, "text": "目前現場只有這兩款"},
    ]
    out = filters.clean_segments(segs)
    assert out[0]["text"] == "你好"


def test_speaker_for_uses_largest_overlap():
    turns = [(0, 3, "男"), (3, 6, "女")]
    assert filters.speaker_for(2.5, 4.0, turns) == "女"
    assert filters.speaker_for(0.5, 1.0, turns) == "男"


def test_merge_blocks_splits_on_speaker_change():
    segs = [
        {"start": 0, "end": 2, "text": "我想要找機械表"},
        {"start": 2, "end": 3, "text": "那面盤呢"},
        {"start": 3, "end": 5, "text": "現場只有兩款"},
    ]
    turns = [(0, 2.2, "男"), (2.2, 5, "女")]
    blocks = filters.merge_blocks(segs, turns)
    assert [b["label"] for b in blocks] == ["男", "女"]
    assert blocks[1]["text"] == "那面盤呢現場只有兩款"


def test_merge_blocks_without_speakers_merges_by_gap():
    segs = [
        {"start": 0, "end": 2, "text": "第一句"},
        {"start": 2.5, "end": 4, "text": "第二句"},
        {"start": 10, "end": 12, "text": "很久之後"},
    ]
    blocks = filters.merge_blocks(segs, None)
    assert len(blocks) == 2
    assert blocks[0]["text"] == "第一句第二句"


def test_proofread_chunks_and_prefix():
    import proofread

    lines = [f"[{i:02d}:00] 男：這是第 {i} 句" for i in range(30)]
    chunks = list(proofread._chunks(lines, limit=200))
    assert sum(len(c) for c in chunks) == 30
    assert all(len("\n".join(c)) <= 260 for c in chunks)
    assert proofread._prefix("[00:12] 男：你好") == "[00:12] 男："
    assert proofread._prefix("[01:05] 女：嗯") == "[01:05] 女："
