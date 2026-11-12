"""轉錄＋語者分離的實際流程。"""
import os
import subprocess

import numpy as np
from filters import clean_segments, merge_blocks

HF_TOKEN_PATH = os.path.expanduser("~/.cache/huggingface/token")
ASR_MODEL = "mlx-community/whisper-large-v3-turbo"


def duration(src):
    """回傳音檔長度（秒）。"""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", src], capture_output=True, timeout=60)
    try:
        return float(out.stdout.decode().strip())
    except ValueError:
        return 0.0


def normalize(src, dst, skip_start=0, skip_end=0):
    """任何格式 → 16k 單聲道 wav；可跳過頭尾（客戶常說前後沒重點）。"""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if skip_start:
        cmd += ["-ss", str(skip_start)]
    cmd += ["-i", src]
    if skip_start or skip_end:
        total = duration(src)
        if total:
            keep = max(1.0, total - skip_start - skip_end)
            cmd += ["-t", str(keep)]
    cmd += ["-ac", "1", "-ar", "16000", dst]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(f"音訊轉檔失敗：{detail or 'ffmpeg 無輸出'}")
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
    """pyannote 的 SPEAKER_00/01 → 男/女：平均音高較高者為女。

    只讀每一段的前 2 秒（wave 隨機存取），避免把整顆錄音載進記憶體。
    """
    import wave as wavemod

    per = {}
    with wavemod.open(wav, "rb") as f:
        sr = f.getframerate()
        total = f.getnframes()
        for t_start, t_end, label in turns[:600]:
            start = int(max(0.0, t_start) * sr)
            end = int(min(total / sr, t_end) * sr)
            end = min(end, start + 2 * sr)
            if end - start < sr * 0.2:
                continue
            f.setpos(start)
            audio = np.frombuffer(f.readframes(end - start), dtype=np.int16)
            f0 = _median_f0(audio.astype(np.float32) / 32768.0, sr)
            if f0:
                per.setdefault(label, []).append(f0)
    means = {k: float(np.mean(v)) for k, v in per.items() if v}
    if len(means) < 2 or len(means) > 2:
        # 少於兩人沒得比；超過兩人硬壓成男女會把不同人併成同一人
        return turns, means
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
        try:
            import torch
            if torch.backends.mps.is_available():
                pipe.to(torch.device("mps"))   # CPU 跑 52 分鐘要 30+ 分鐘
        except Exception:
            pass
        kwargs = {"num_speakers": num_speakers} if num_speakers else {}
        out = pipe(wav, **kwargs)
        # pyannote 4.x 回傳 DiarizeOutput；3.x 直接回 Annotation
        ann = getattr(out, "speaker_diarization", out)
        turns = [(float(t.start), float(t.end), s) for t, _, s in ann.itertracks(yield_label=True)]
        turns, means = name_speakers_by_pitch(wav, turns)
        return turns, None
    except Exception as e:      # 任何失敗都降級成「不分軌」
        return None, f"{type(e).__name__}: {e}"


def build_text(wav_name, segs, turns, error=None, elapsed=None, polish=False, offset=0):
    blocks = merge_blocks(segs, turns)
    lines = [f"# 逐字稿：{wav_name}"]
    if turns and all(t[2] in ("男", "女") for t in turns):
        lines.append("# 語者：男／女（依整段平均音高判定）")
    elif turns:
        lines.append(f"# 語者：自動分群 {len({t[2] for t in turns})} 位（未對應男女）")
    else:
        reason = "語者分離模型尚未授權（Hugging Face）" if error and "GatedRepoError" in error else (error or "不可用")
        lines.append(f"# 未分軌：{reason}")
    if elapsed:
        lines.append(f"# 處理時間 {elapsed:.0f} 秒")
    lines.append("")

    def ts(x):
        m, sec = divmod(int(x + offset), 60)     # 裁切過的話，時間碼對回原檔
        return f"{m:02d}:{sec:02d}"

    body = []
    prev_end = None
    for b in blocks:
        if prev_end is not None and b["start"] - prev_end > 90:
            body.append({"gap": True, "text": f"[{ts(prev_end)}] （此段 {ts(b['start'] - prev_end)} 無可辨識語音）"})
        who = f"{b['label']}：" if b["label"] else ""
        body.append({"gap": False, "text": f"[{ts(b['start'])}] {who}{b['text']}"})
        prev_end = b["end"]

    if polish and body:
        from proofread import proofread_lines
        content = [b["text"] for b in body if not b["gap"]]
        fixed, note = proofread_lines(content)
        it = iter(fixed)
        for b in body:
            if not b["gap"]:
                b["text"] = next(it, b["text"])
        lines.append(f"# {note}")
        lines.append("# ⓘ = 無法確定、保留原文；請對照錄音確認")

    lines.extend(b["text"] for b in body)
    return "\n".join(lines) + "\n", len(blocks)
