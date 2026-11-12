"""語音轉逐字稿（本機）— Flask 服務，port 5975。

上傳錄音 → mlx-whisper 轉文字 → pyannote 分語者（有授權才啟用）→ 下載逐字稿。
檔案只留在本機，不會上傳到任何外部服務。
"""
import json
import os
import queue
import secrets
import threading
import time

import pipeline
from flask import Flask, jsonify, redirect, render_template, request, send_file

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
ALLOWED = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4"}
MAX_MB = 500

os.makedirs(DATA, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

_jobs = {}
_jobs_lock = threading.Lock()
_queue = queue.Queue()


def _job_dir(job_id):
    return os.path.join(DATA, job_id)


def _save_meta(job_id):
    with _jobs_lock:
        meta = dict(_jobs[job_id])
    meta.pop("_path", None)
    with open(os.path.join(_job_dir(job_id), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def _set(job_id, **kw):
    with _jobs_lock:
        _jobs[job_id].update(kw)
    _save_meta(job_id)


def _run_job(job_id):
    meta = _jobs[job_id]
    started = time.time()
    src = meta["src_path"]
    d = _job_dir(job_id)

    def progress(state, message):
        _set(job_id, state=state, message=message)

    try:
        progress("transcribing", "整理音訊…")
        wav = pipeline.normalize(src, os.path.join(d, "audio.wav"),
                                 skip_start=meta.get("skip_start") or 0,
                                 skip_end=meta.get("skip_end") or 0)

        segs = pipeline.transcribe(wav, progress)
        _set(job_id, segments=len(segs), asr_done=True)

        turns, err = pipeline.diarize(wav, progress,
                                      num_speakers=(meta.get("speakers") or None))
        elapsed = time.time() - started
        text, blocks = pipeline.build_text(meta.get("name") or os.path.basename(src),
                                           segs, turns, err, elapsed,
                                           polish=bool(meta.get("polish")),
                                           offset=meta.get("skip_start") or 0)

        out = os.path.join(d, "transcript.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        friendly = "完成" if turns else ("完成（語者模型未授權）" if err and "GatedRepoError" in err else "完成（未分軌）")
        try:
            os.remove(wav)          # 轉完就刪，原始檔保留供重跑
        except OSError:
            pass
        _set(job_id, state="done", message=friendly,
             blocks=blocks, has_speakers=bool(turns), diarize_error=err,
             elapsed=round(elapsed), output=out)
    except Exception as e:      # 任何失敗都要留下原因
        _set(job_id, state="error", message=f"{type(e).__name__}: {e}")


def _worker():
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        except Exception as e:      # 任何例外都不能讓唯一的 worker 死掉
            try:
                _set(job_id, state="error", message=f"{type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            _queue.task_done()


def _load_existing_jobs():
    """重啟後把 data/ 內的 meta.json 讀回來，首頁才不會空白。"""
    if not os.path.isdir(DATA):
        return
    for name in os.listdir(DATA):
        meta_path = os.path.join(DATA, name, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        meta["id"] = name
        meta.pop("src_path", None)
        if meta.get("state") in ("queued", "transcribing", "diarizing"):
            meta["state"] = "error"
            meta["message"] = "服務重啟，任務中斷"
        _jobs[name] = meta


_load_existing_jobs()
threading.Thread(target=_worker, daemon=True, name="transcribe-worker").start()


@app.get("/")
def index():
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.get("created", 0), reverse=True)[:10]
    return render_template("index.html", jobs=jobs, max_mb=MAX_MB)


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "沒有檔案"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED:
        return jsonify({"ok": False, "error": f"不支援的格式：{ext}"}), 400

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    d = _job_dir(job_id)
    os.makedirs(d, exist_ok=True)
    display = os.path.basename(f.filename) or f"audio{ext}"   # 保留中文原名
    src = os.path.join(d, f"source{ext}")                      # 存檔用安全名稱
    f.save(src)

    def _sec(key):
        """'5:00' 或 '300' → 秒。"""
        raw = (request.form.get(key) or "").strip()
        if not raw:
            return 0
        try:
            if ":" in raw:
                m, s2 = raw.split(":", 1)
                return int(m) * 60 + int(s2)
            return int(float(raw))
        except ValueError:
            return 0

    try:
        want = int(request.form.get("speakers", "0") or 0)
    except ValueError:
        want = 0
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id, "name": display, "state": "queued", "message": "排隊中",
            "created": time.time(), "src_path": src, "has_speakers": False,
            "speakers": want if want >= 2 else None,
            "polish": request.form.get("polish", "") == "1",
            "skip_start": _sec("skip_start"),
            "skip_end": _sec("skip_end"),
        }
    _save_meta(job_id)
    _queue.put(job_id)
    return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/job/<job_id>")
def job_status(job_id):
    with _jobs_lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None
    if not job:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "job": {k: v for k, v in job.items() if not k.startswith("_")}})


@app.get("/download/<job_id>")
def download(job_id):
    path = os.path.join(_job_dir(job_id), "transcript.txt")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "尚未完成"}), 404
    return send_file(path, as_attachment=True,
                     download_name=f"{job_id}_逐字稿.txt", mimetype="text/plain; charset=utf-8")


@app.get("/swap/<job_id>")
def swap(job_id):
    """互換男/女標籤：音高相近時自動判定可能顛倒，讓使用者一鍵修正。"""
    path = os.path.join(_job_dir(job_id), "transcript.txt")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "尚未完成"}), 404
    text = open(path, encoding="utf-8").read()
    text = text.replace("] 男：", "] @TMP@：").replace("] 女：", "] 男：").replace("] @TMP@：", "] 女：")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return redirect(f"/view/{job_id}")


@app.get("/view/<job_id>")
def view(job_id):
    path = os.path.join(_job_dir(job_id), "transcript.txt")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "尚未完成"}), 404
    return open(path, encoding="utf-8").read(), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/api/health")
def health():
    with _jobs_lock:
        running = [j["id"] for j in _jobs.values() if j["state"] in ("transcribing", "diarizing")]
    return jsonify({"ok": True, "queue": _queue.qsize(), "running": running,
                    "diarize_ready": _diarize_ready()})


_diarize_cache = {"ts": 0.0, "ready": False}


def _diarize_ready():
    """最多每 60 秒檢查一次；HF 不可達時不要拖慢健康檢查。"""
    now = time.time()
    if now - _diarize_cache["ts"] < 60:
        return _diarize_cache["ready"]
    try:
        token = open(pipeline.HF_TOKEN_PATH, encoding="utf-8").read().strip()
        from huggingface_hub import hf_hub_download
        hf_hub_download("pyannote/speaker-diarization-community-1", "plda/plda.npz", token=token)
        ready = True
    except Exception:
        ready = False
    _diarize_cache.update(ts=now, ready=ready)
    return ready


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5975)
