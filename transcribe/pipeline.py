"""轉錄＋語者分離的實際流程。"""
import os
import subprocess

import numpy as np
from filters import clean_segments, merge_blocks

HF_TOKEN_PATH = os.path.expanduser("~/.cache/huggingface/token")
ASR_MODEL = "mlx-community/whisper-large-v3-turbo"


def normalize(src, dst):
    """任何格式 → 16k 單聲道 wav。"""
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", src, "-ac", "1", "-ar", "16000", dst],
                   check=True, timeout=1800)
    return dst


def transcribe(wav, progress=None):
    import mlx_whisper
    if progress:
        progress("transcribing", "語音轉文字中…")
    result = mlx_whisper.transcribe(
        wav,
        path_or_hf_repo=ASR_MODEL,
        language="zh",
        task="transcribe",
        word_timestamps=False,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        verbose=False,
    )
    segs = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]}
            for s in result.get("segments", []) if s.get("text", "").strip()]
    return clean_segments(segs)


def _median_f0(chunk, sr=16000):
    frame = 640
    if len(chunk) < frame:
        return None
    span = len(chunk) - frame
    picks = np.linspace(0, span, min(10, max(3, span // 1600 + 1))).astype(int)
    vals = []
    for i in picks:
        fr = chunk[i:i + frame]
        if np.sqrt((fr ** 2).mean()) < 0.006:
            continue
        fr = fr - fr.mean()
        n = len(fr)
        s = np.fft.rfft(fr, 2 * n)
        ac = np.fft.irfft(s * np.conj(s))[:n]
        if ac[0] <= 0:
            continue
        lo, hi = int(sr / 300), int(sr / 70)
        seg = ac[lo:hi]
        if not len(seg):
            continue
        best = float(seg.max())
        if best / ac[0] < 0.4:
            continue
        cand = np.where(seg >= 0.9 * best)[0]
        lag = int(cand[0] if len(cand) else np.argmax(seg)) + lo
        f = sr / lag
        if 70 <= f <= 300:
            vals.append(f)
    return float(np.median(vals)) if vals else None


def name_speakers_by_pitch(wav, turns):
    """pyannote 的 SPEAKER_00/01 → 男/女：整段平均音高較高者為女。"""
    import wave as wavemod

    with wavemod.open(wav, "rb") as f:
        sr = f.getframerate()
        raw = f.readframes(f.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    per = {}
    for t_start, t_end, label in turns:
        clip = audio[int(t_start * sr):int(min(len(audio) / sr, t_end) * sr)]
        f0 = _median_f0(clip, sr)
        if f0:
            per.setdefault(label, []).append(f0)
    means = {k: float(np.mean(v)) for k, v in per.items() if v}
    if len(means) < 2:
        return turns, {}
    female = max(means, key=means.get)
    mapping = {k: ("女" if k == female else "男") for k in means}
    return [(a, b, mapping.get(c, c)) for a, b, c in turns], means


def diarize(wav, progress=None, num_speakers=None):
    """回傳 (turns, error)；沒有授權或失敗時 turns 為 None，轉錄結果仍可用。"""
    if progress:
        progress("diarizing", "辨識說話者中…")
    try:
        from pyannote.audio import Pipeline
        token = open(HF_TOKEN_PATH, encoding="utf-8").read().strip()
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
        kwargs = {"num_speakers": num_speakers} if num_speakers else {}
        out = pipe(wav, **kwargs)
        # pyannote 4.x 回傳 DiarizeOutput；3.x 直接回 Annotation
        ann = getattr(out, "speaker_diarization", out)
        turns = [(float(t.start), float(t.end), s) for t, _, s in ann.itertracks(yield_label=True)]
        turns, means = name_speakers_by_pitch(wav, turns)
        return turns, None
    except Exception as e:      # 任何失敗都降級成「不分軌」
        return None, f"{type(e).__name__}: {e}"


def build_text(wav_name, segs, turns, error=None, elapsed=None):
    blocks = merge_blocks(segs, turns)
    lines = [f"# 逐字稿：{wav_name}"]
    if turns:
        lines.append("# 語者：男／女（依整段平均音高判定）")
    else:
        reason = "語者分離模型尚未授權（Hugging Face）" if error and "GatedRepoError" in error else (error or "不可用")
        lines.append(f"# 未分軌：{reason}")
    if elapsed:
        lines.append(f"# 處理時間 {elapsed:.0f} 秒")
    lines.append("")

    def ts(x):
        m, sec = divmod(int(x), 60)
        return f"{m:02d}:{sec:02d}"

    prev_end = None
    for b in blocks:
        if prev_end is not None and b["start"] - prev_end > 90:
            lines.append(f"[{ts(prev_end)}] （此段 {ts(b['start'] - prev_end)} 無可辨識語音）")
        who = f"{b['label']}：" if b["label"] else ""
        lines.append(f"[{ts(b['start'])}] {who}{b['text']}")
        prev_end = b["end"]
    return "\n".join(lines) + "\n", len(blocks)
