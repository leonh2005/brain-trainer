"""Mac 端 ComfyUI 薄層：表單送工作流、輪詢狀態、預覽與下載影片。"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import websocket
from flask import Flask, Response, abort, jsonify, render_template, request

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:18188").rstrip("/")
WS_URL = COMFY_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
WORKFLOW_DIR = Path(__file__).resolve().parent / "workflows"
PROGRESS: dict[str, dict] = {}
LAST_PROMPT_ID: str | None = None
WORKFLOW_INFO = {
    "01_t2v_standard.api.json": (
        "標準文生影片",
        "768×512、97 幀（約 4 秒）、20 步。畫質最好，但 4 核 CPU 會跑很久。",
    ),
    "02_t2v_fast.api.json": (
        "快速試稿",
        "576×320、25 幀（約 1 秒）、8 步。最快，適合先試提示詞與鏡頭。",
    ),
    "03_t2v_long.api.json": (
        "長鏡頭",
        "768×512、121 幀（約 5 秒）、20 步。適合完整動作或連續運鏡。",
    ),
    "04_t2v_wide.api.json": (
        "寬銀幕",
        "1280×704、121 幀、20 步。電影感構圖，六個裡最慢。",
    ),
    "05_i2v_standard.api.json": (
        "圖生影片（標準）",
        "768×512、49 幀（約 2 秒）、12 步。需先上傳圖片，填入圖片檔名。",
    ),
    "06_i2v_fast.api.json": (
        "圖生影片（快速）",
        "576×320、25 幀、8 步。需先上傳圖片，適合讓靜態圖動起來試效果。",
    ),
}

SIZE_PRESETS = {
    "768x512": ("標準橫式 3:2", 768, 512),
    "1024x576": ("HD 橫式 16:9", 1024, 576),
    "1280x704": ("寬銀幕 16:9", 1280, 704),
    "704x1280": ("直式 9:16", 704, 1280),
    "576x1024": ("HD 直式 9:16", 576, 1024),
    "768x768": ("方形 1:1", 768, 768),
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


def _post_json(path: str, payload: dict) -> dict:
    """送出 JSON 到 ComfyUI，回傳其 JSON 回應。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY_URL}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _get_json(path: str) -> dict:
    """讀取 ComfyUI JSON；history 尚未建立時回空 dict。"""
    try:
        with urllib.request.urlopen(f"{COMFY_URL}{path}", timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def _get_bytes(url: str) -> bytes:
    """從 ComfyUI /view 抓回檔案內容。"""
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def _upload_image(file_storage) -> dict:
    """把選擇的圖片上傳到 ComfyUI input，回傳檔名資訊。"""
    filename = Path(file_storage.filename or "image.png").name
    boundary = uuid.uuid4().hex
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {file_storage.mimetype or 'application/octet-stream'}\r\n\r\n"
    ).encode()
    body = header + file_storage.read() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _running_ids() -> set[str]:
    """回傳 ComfyUI 目前正在執行的 prompt id。"""
    try:
        queue = _get_json("/queue")
    except Exception:
        return set()
    return {
        str(item[1])
        for item in queue.get("queue_running", [])
        if isinstance(item, list) and len(item) > 1
    }


def _start_progress_listener(prompt_id: str, client_id: str) -> None:
    """背景監聽 ComfyUI WebSocket 的 progress_state，寫入 PROGRESS。"""

    def listen() -> None:
        deadline = time.time() + 7200
        while time.time() < deadline:
            try:
                ws = websocket.create_connection(
                    f"{WS_URL}?clientId={client_id}", timeout=10
                )
                ws.settimeout(10)
                while time.time() < deadline:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    data = message.get("data") or {}
                    if data.get("prompt_id") != prompt_id:
                        continue
                    kind = message.get("type")
                    if kind == "progress_state":
                        nodes = data.get("nodes") or {}
                        best = max(
                            (node for node in nodes.values() if node.get("max")),
                            key=lambda node: node["value"] / node["max"],
                            default=None,
                        )
                        if best:
                            percent = min(99, int(100 * best["value"] / best["max"]))
                            PROGRESS[prompt_id] = {
                                "percent": percent,
                                "phase": "取樣中",
                            }
                    elif kind in ("execution_success", "execution_error"):
                        PROGRESS[prompt_id] = {
                            "percent": 100,
                            "phase": "完成" if kind == "execution_success" else "錯誤",
                        }
                        return
                ws.close()
            except Exception:
                time.sleep(3)
        PROGRESS.pop(prompt_id, None)

    threading.Thread(
        target=listen, daemon=True, name=f"comfy-progress-{prompt_id[:8]}"
    ).start()


def _current_prompt_id() -> str | None:
    """頁面重載後仍可接回最近一筆（執行中或已完成）任務。"""
    if LAST_PROMPT_ID:
        return LAST_PROMPT_ID
    running = _running_ids()
    if running:
        return next(iter(running), None)
    try:
        history = _get_json("/history")
    except Exception:
        return None
    return next(reversed(list(history)), None)


def _workflow_names() -> list[str]:
    return sorted(path.name for path in WORKFLOW_DIR.glob("*.api.json"))


def _workflow_options() -> list[dict[str, str]]:
    options = []
    for name in _workflow_names():
        title, description = WORKFLOW_INFO.get(name, (name, "自訂工作流"))
        options.append({"filename": name, "title": title, "description": description})
    return options


def _size_options() -> list[dict[str, str | int]]:
    return [
        {"value": value, "label": label, "width": width, "height": height}
        for value, (label, width, height) in SIZE_PRESETS.items()
    ]


def _load_workflow(name: str) -> dict:
    if name not in _workflow_names():
        abort(400, "未知的工作流")
    return json.loads((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


def _form_int(
    form, key: str, default: int, low: int, high: int, multiple: int | None = None
) -> int:
    raw = form.get(key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        abort(400, f"{key} 必須是整數")
    if not low <= value <= high:
        abort(400, f"{key} 超出範圍")
    if multiple and value % multiple:
        abort(400, f"{key} 必須是 {multiple} 的倍數")
    return value


def _patch_workflow(workflow: dict, values: dict) -> dict:
    """把表單值寫入 API 工作流（原地修改剛讀出的 Python 物件）。"""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        kind = node.get("class_type")
        if kind == "CLIPTextEncode":
            if inputs.get("text") == "__PROMPT__":
                inputs["text"] = values["prompt"]
            elif inputs.get("text") == "__NEGATIVE__":
                inputs["text"] = values["negative"]
        elif kind in ("EmptyLTXVLatentVideo", "LTXVImgToVideo"):
            inputs["width"] = values["width"]
            inputs["height"] = values["height"]
            inputs["length"] = values["frames"]
        elif kind == "KSampler":
            inputs["seed"] = values["seed"]
            inputs["steps"] = values["steps"]
        elif kind == "SamplerCustom":
            inputs["noise_seed"] = values["seed"]
        elif kind == "LTXVScheduler":
            inputs["steps"] = values["steps"]
        elif kind == "LoadImage" and values["image"]:
            inputs["image"] = values["image"]
        elif kind == "SaveVideo":
            inputs["filename_prefix"] = f"comfy_web/{int(time.time())}"
    return workflow


def _completion_info(entry: dict) -> dict:
    """從 ComfyUI history 的 execution_success 取出完成時間與耗時。"""
    start = success = None
    for kind, payload in entry.get("status", {}).get("messages", []):
        if not isinstance(payload, dict):
            continue
        if kind == "execution_start":
            start = payload.get("timestamp")
        elif kind == "execution_success":
            success = payload.get("timestamp")
    if not success:
        return {}
    info = {
        "completed_at": datetime.fromtimestamp(success / 1000).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    }
    if start:
        info["duration_seconds"] = round((success - start) / 1000)
    return info


def _output_item(prompt_id: str) -> dict | None:
    history = _get_json(f"/history/{prompt_id}")
    outputs = (history.get(prompt_id) or {}).get("outputs", {})
    for node in outputs.values():
        for key in ("videos", "images", "gifs"):
            items = node.get(key)
            if items:
                return items[0]
    return None


def _view_url(item: dict) -> str:
    params = urllib.parse.urlencode(
        {
            "filename": Path(item["filename"]).name,
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }
    )
    return f"{COMFY_URL}/view?{params}"


@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        workflows=_workflow_options(),
        sizes=_size_options(),
        last_id=_current_prompt_id(),
    )


@app.post("/generate")
def generate():
    form = request.form
    prompt = form.get("prompt", "").strip()
    if not prompt:
        abort(400, "prompt 不可為空")
    name = form.get("workflow", "")
    workflow = _load_workflow(name)
    size = form.get("size", "768x512")
    if size not in SIZE_PRESETS:
        abort(400, "未知的尺寸配對")
    _, width, height = SIZE_PRESETS[size]
    uploaded = request.files.get("image_file")
    image_name = _upload_image(uploaded).get("name", "") if uploaded and uploaded.filename else ""
    values = {
        "prompt": prompt,
        "negative": form.get("negative", "").strip(),
        "width": width,
        "height": height,
        "frames": _form_int(form, "frames", 25, 9, 257),
        "steps": _form_int(form, "steps", 8, 1, 100),
        "seed": _form_int(form, "seed", 0, 0, 2**64 - 1),
        "image": image_name,
    }
    workflow = _patch_workflow(workflow, values)
    client_id = uuid.uuid4().hex
    result = _post_json("/prompt", {"prompt": workflow, "client_id": client_id})
    prompt_id = result["prompt_id"]
    global LAST_PROMPT_ID
    LAST_PROMPT_ID = prompt_id
    _start_progress_listener(prompt_id, client_id)
    return jsonify({"prompt_id": prompt_id})


@app.get("/status/<prompt_id>")
def status(prompt_id: str):
    entry = _get_json(f"/history/{prompt_id}").get(prompt_id)
    if entry and entry.get("status", {}).get("status_str") == "error":
        return jsonify({"status": "error", "percent": 100, "phase": "錯誤"})
    if entry and entry.get("outputs"):
        PROGRESS.pop(prompt_id, None)
        return jsonify(
            {
                "status": "completed",
                "percent": 100,
                "outputs": entry["outputs"],
                **_completion_info(entry),
            }
        )
    if prompt_id in _running_ids():
        info = PROGRESS.get(prompt_id, {})
        return jsonify(
            {
                "status": "running",
                "percent": info.get("percent", 0),
                "phase": info.get("phase", "準備中"),
            }
        )
    return jsonify({"status": "queued", "percent": 0, "phase": "等待中"})


@app.get("/video/<prompt_id>")
def video(prompt_id: str):
    item = _output_item(prompt_id)
    if not item:
        abort(404, "影片尚未產生")
    suffix = Path(item["filename"]).suffix.lower()
    mimetype = "video/mp4" if suffix == ".mp4" else "application/octet-stream"
    return Response(_get_bytes(_view_url(item)), mimetype=mimetype)


@app.get("/download/<prompt_id>")
def download(prompt_id: str):
    item = _output_item(prompt_id)
    if not item:
        abort(404, "影片尚未產生")
    filename = Path(item["filename"]).name
    response = Response(_get_bytes(_view_url(item)), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5981)
