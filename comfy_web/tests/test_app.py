"""comfy_web Flask 薄層的單元測試（TDD：先寫，預期 RED）。"""

import json

import pytest

WORKFLOW = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__", "clip": ["2", 0]}},
    "2": {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": 768, "height": 512, "length": 97, "batch_size": 1},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 30,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["1", 0],
            "negative": ["5", 0],
            "latent_image": ["2", 0],
        },
    },
    "4": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "ltx.gguf"}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "__NEGATIVE__", "clip": ["2", 0]}},
    "6": {
        "class_type": "SaveVideo",
        "inputs": {"video": ["7", 0], "filename_prefix": "video/ComfyUI", "format": "auto"},
    },
    "7": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["9", 0]}},
    "9": {"class_type": "VAELoader", "inputs": {"vae_name": "ltx.safetensors"}},
}

HISTORY = {
    "pid1": {
        "outputs": {"6": {"videos": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]}},
        "status": {
            "status_str": "success",
            "messages": [
                ["execution_start", {"prompt_id": "pid1", "timestamp": 1795787573352}],
                ["execution_success", {"prompt_id": "pid1", "timestamp": 1795787931865}],
            ],
        },
    }
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    """建立隔離的 Flask client，所有對 ComfyUI 的 HTTP 呼叫都換成假資料。"""
    import comfy_web.app as app_module

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "t2v.api.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    monkeypatch.setattr(app_module, "WORKFLOW_DIR", workflow_dir)

    captured = {}
    monkeypatch.setattr(
        app_module,
        "_post_json",
        lambda path, payload: (captured.update(payload), {"prompt_id": "pid1"})[1],
    )
    monkeypatch.setattr(app_module, "_get_json", lambda path: HISTORY if "pid1" in path else {})
    monkeypatch.setattr(app_module, "_get_bytes", lambda url: b"\x00\x00\x00 ftypmp42")
    monkeypatch.setattr(app_module, "_start_progress_listener", lambda *args: None)
    app_module.PROGRESS.clear()
    app_module.LAST_PROMPT_ID = None

    app_module.app.config["TESTING"] = True
    return app_module.app.test_client(), captured


def test_index_lists_workflows(client):
    test_client, _ = client
    response = test_client.get("/")
    assert response.status_code == 200
    assert b"t2v.api.json" in response.data


def test_index_shows_workflow_description(client):
    test_client, _ = client
    response = test_client.get("/")
    assert b"data-description=" in response.data
    assert "自訂工作流".encode() in response.data


def test_index_uses_friendly_title(monkeypatch, tmp_path):
    import comfy_web.app as app_module

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "01_t2v_standard.api.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_module, "WORKFLOW_DIR", workflow_dir)
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().get("/")
    assert "標準文生影片".encode() in response.data
    assert "768×512".encode() in response.data


def test_generate_patches_prompt_and_options(client):
    test_client, captured = client
    response = test_client.post(
        "/generate",
        data={
            "prompt": "a fox in the rain",
            "negative": "blurry",
            "workflow": "t2v.api.json",
            "size": "1024x576",
            "frames": "25",
            "steps": "8",
            "seed": "42",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["prompt_id"] == "pid1"
    workflow = captured["prompt"]
    assert workflow["1"]["inputs"]["text"] == "a fox in the rain"
    assert workflow["5"]["inputs"]["text"] == "blurry"
    assert workflow["2"]["inputs"]["width"] == 1024
    assert workflow["2"]["inputs"]["height"] == 576
    assert workflow["2"]["inputs"]["length"] == 25
    assert workflow["3"]["inputs"]["steps"] == 8
    assert workflow["3"]["inputs"]["seed"] == 42


def test_index_resumes_last_job(client, monkeypatch):
    import comfy_web.app as app_module

    app_module.LAST_PROMPT_ID = "pid1"
    app_module.app.config["TESTING"] = True
    response = app_module.app.test_client().get("/")
    assert b"pid1" in response.data
    assert b"setInterval" in response.data


def test_generate_records_last_prompt_id(client):
    test_client, _ = client
    import comfy_web.app as app_module

    test_client.post(
        "/generate",
        data={"prompt": "cat", "workflow": "t2v.api.json", "size": "768x512"},
    )
    assert app_module.LAST_PROMPT_ID == "pid1"


def test_generate_rejects_empty_prompt(client):
    test_client, _ = client
    response = test_client.post("/generate", data={"prompt": "   ", "workflow": "t2v.api.json"})
    assert response.status_code == 400


def test_generate_rejects_unknown_size(client):
    test_client, _ = client
    response = test_client.post(
        "/generate", data={"prompt": "cat", "workflow": "t2v.api.json", "size": "100x100"}
    )
    assert response.status_code == 400


def test_index_shows_size_presets(client):
    test_client, _ = client
    response = test_client.get("/")
    assert b'name="size"' in response.data
    assert b"768x512" in response.data


def test_status_maps_completed(client):
    test_client, _ = client
    response = test_client.get("/status/pid1")
    assert response.status_code == 200
    assert response.get_json()["status"] == "completed"


def test_status_includes_completion_time(client):
    from datetime import datetime

    test_client, _ = client
    data = test_client.get("/status/pid1").get_json()
    expected = datetime.fromtimestamp(1795787931865 / 1000).strftime("%Y-%m-%d %H:%M:%S")
    assert data["completed_at"] == expected
    assert data["duration_seconds"] == 359


def test_status_completed_without_messages_still_works(client, monkeypatch):
    import comfy_web.app as app_module

    history = {
        "pid1": {
            "outputs": {"6": {"videos": [{"filename": "out.mp4"}]}},
            "status": {"status_str": "success"},
        }
    }
    monkeypatch.setattr(app_module, "_get_json", lambda path: history if "pid1" in path else {})
    app_module.app.config["TESTING"] = True

    data = app_module.app.test_client().get("/status/pid1").get_json()
    assert data["status"] == "completed"
    assert "completed_at" not in data


def test_status_maps_queued(client, monkeypatch):
    import comfy_web.app as app_module

    monkeypatch.setattr(app_module, "_get_json", lambda path: {})
    app_module.app.config["TESTING"] = True
    response = app_module.app.test_client().get("/status/unknown")
    assert response.status_code == 200
    assert response.get_json()["status"] == "queued"


def test_status_maps_running_with_percent(client, monkeypatch):
    import comfy_web.app as app_module

    queue = {"queue_running": [["x", "pid2", {}]], "queue_pending": []}
    monkeypatch.setattr(
        app_module,
        "_get_json",
        lambda path: queue if path == "/queue" else {},
    )
    app_module.PROGRESS["pid2"] = {"percent": 42, "phase": "取樣中"}
    app_module.app.config["TESTING"] = True

    response = app_module.app.test_client().get("/status/pid2")
    data = response.get_json()
    assert data["status"] == "running"
    assert data["percent"] == 42
    assert data["phase"] == "取樣中"


def test_status_queued_has_zero_percent(client):
    test_client, _ = client
    data = test_client.get("/status/unknown").get_json()
    assert data == {"status": "queued", "percent": 0, "phase": "等待中"}


def test_download_sets_attachment_and_mp4(client):
    test_client, _ = client
    response = test_client.get("/download/pid1")
    assert response.status_code == 200
    assert response.data == b"\x00\x00\x00 ftypmp42"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "out.mp4" in response.headers["Content-Disposition"]


def test_video_returns_mp4_content_type(client):
    test_client, _ = client
    response = test_client.get("/video/pid1")
    assert response.status_code == 200
    assert response.mimetype == "video/mp4"
