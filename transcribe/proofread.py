"""用 LLM 校對 ASR 逐字稿：只修同音錯字與標點，不新增內容。

保留每行的 [時間] 與語者前綴；輸出格式必須一致，行數不同就整批退回原文。
"""
import json
import os
import re
import urllib.request

KEY_PATH = os.path.expanduser("~/CCProject/.secrets/gemini_key.txt")
MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]
MAX_CHARS = 1200

PROMPT = """你是逐字稿校對員。以下是一段對話的語音辨識結果，每行格式是「[時間] 語者：文字」。

只做兩件事：
1. 修正明顯的同音錯字（依上下文判斷，例如「小矫徑」→「小錶徑」）
2. 補上正確的標點

嚴格規則：
- 不要新增原文沒有的內容、不要摘要、不要改寫語氣
- 保留所有口語、語助詞與重複
- 無法判斷的字保留原樣，並在該字後面加上 ⓘ
- 輸出必須是「一行對一行」，每行都要保留原本的 [時間] 與語者前綴
- 只輸出校對後的行，不要任何說明

原文：
"""


def _call(text, key):
    body = json.dumps({"contents": [{"parts": [{"text": PROMPT + text}]}]}).encode()
    last = None
    for model in MODELS:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:      # 額度用完換下一個模型
            last = e
            continue
    raise RuntimeError(f"校對失敗：{last}")


def _chunks(lines, limit=MAX_CHARS):
    buf, size = [], 0
    for line in lines:
        if size + len(line) > limit and buf:
            yield buf
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        yield buf


def proofread_lines(lines, key=None):
    """lines: ['[00:12] 男：…', …]；回傳校對後的新清單。"""
    key = key or (open(KEY_PATH, encoding="utf-8").read().strip() if os.path.exists(KEY_PATH) else "")
    if not key:
        return lines, "沒有 Gemini 金鑰，略過校對"

    out, fixed_lines, errors = [], 0, 0
    for chunk in _chunks(lines):
        source = "\n".join(chunk)
        try:
            text = _call(source, key)
        except Exception as e:      # 單一區塊失敗就保留原文
            errors += 1
            out.extend(chunk)
            continue
        got = [ln for ln in text.splitlines() if ln.strip()]
        # 只接受「行數一致」且「時間前綴一致」的結果，否則整批退回
        if len(got) != len(chunk) or any(
                _prefix(a) != _prefix(b) for a, b in zip(got, chunk)):
            errors += 1
            out.extend(chunk)
            continue
        for a, b in zip(got, chunk):
            if a != b:
                fixed_lines += 1
            out.append(a.strip())
    msg = f"校對：修正 {fixed_lines} 行" + (f"，{errors} 段退回原文" if errors else "")
    return out, msg


def _prefix(line):
    m = re.match(r"\[(\d+:\d+)\] (男|女|SPEAKER_\d+)：", line)
    return m.group(0) if m else line[:12]
