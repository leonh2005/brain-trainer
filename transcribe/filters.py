"""逐字稿的純邏輯：幻覺過濾與段落合併（不碰音訊、不碰檔案，方便測試）。"""
import collections
import re

BAD_PHRASES = ("中文字幕", "字幕志愿", "明镜", "點點欄目", "点点栏目",
               "Amara", "請不吝", "请不吝")

_CJK = re.compile(r"[一-鿿]")


def is_junk(text):
    """whisper 對音樂／雜訊的典型幻覺。"""
    t = (text or "").strip()
    if len(t) < 2:
        return True
    # 中文對話裡整段沒有中文字又很短（there、OK…）
    if len(t) <= 20 and not _CJK.search(t):
        return True
    if any(b in t for b in BAD_PHRASES):
        return True
    if len(t) >= 6 and len(set(t)) <= 3:
        return True
    if re.search(r"(.{2,4})\1{4,}", t):          # 同一短單元連 5 次以上
        return True
    if len(t) >= 8:                               # 中文字比例過低（韓文/英文幻覺）
        letters = re.sub(r"[\s\d\W]", "", t)
        if letters and len(_CJK.findall(t)) / len(letters) < 0.2:
            return True
    if len(t) >= 6:                               # 3-gram 重複
        grams = [t[i:i + 3] for i in range(len(t) - 2)]
        if grams and collections.Counter(grams).most_common(1)[0][1] / len(grams) > 0.35:
            return True
    return False


def is_repetitive(text):
    """整段只是同一個小單元反覆（超久的超久的…）。"""
    t = re.sub(r"[\s，。！？!?,.]", "", text or "")
    if len(t) < 6:
        return False
    for n in range(1, 21):
        unit = t[:n]
        reps = len(t) // n
        if reps >= 3 and t[:n * reps] == unit * reps and len(t) - n * reps <= n:
            return True
    return False


def clean_segments(segments):
    """丟掉幻覺段，並移除前導的雜訊區（音樂段）。"""
    segs = [s for s in segments
            if not is_junk(s.get("text", "")) and not is_repetitive(s.get("text", ""))]
    # 只有「前兩分鐘幾乎沒有內容」才視為音樂段並跳過；
    # 開頭就是對話時（字數正常）一律保留，避免誤刪問候語
    early_chars = sum(len(s["text"].strip()) for s in segs if s["start"] < 120)
    if early_chars >= 20:
        return segs
    for i, s in enumerate(segs):
        if len(s["text"].strip()) >= 4:
            return segs[i:]
    return segs


def speaker_for(start, end, turns):
    """回傳與該段重疊最多的語者；沒有重疊就沿用 None。"""
    best, best_overlap = None, 0.0
    for t_start, t_end, label in turns:
        overlap = min(end, t_end) - max(start, t_start)
        if overlap > best_overlap:
            best, best_overlap = label, overlap
    return best


def merge_blocks(segments, turns=None, max_gap=2.0):
    """把逐段文字併成好讀的區塊；有語者資訊時同人連續才合併。"""
    blocks = []
    last_label = None
    for s in segments:
        label = speaker_for(s["start"], s["end"], turns) if turns else None
        if label is None:
            label = last_label
        if (blocks and blocks[-1]["label"] == label
                and s["start"] - blocks[-1]["end"] < max_gap):
            blocks[-1]["text"] += s["text"].strip()
            blocks[-1]["end"] = s["end"]
        else:
            blocks.append({"start": s["start"], "end": s["end"],
                           "label": label, "text": s["text"].strip()})
        last_label = label
    return blocks
