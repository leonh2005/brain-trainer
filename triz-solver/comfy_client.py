# -*- coding: utf-8 -*-
"""ComfyUI 本機圖像生成 client：送 workflow → 輪詢 → 取回產出的 PNG bytes。"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

COMFY_URL = "http://127.0.0.1:8188"
CHECKPOINT = "sd_xl_base_1.0.safetensors"
NEGATIVE_PROMPT = "photo, realistic, 3d render, blurry, text, watermark, extra limbs, deformed"


def is_available() -> bool:
    try:
        urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def _build_workflow(prompt_text: str, seed: int) -> dict:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": 20, "cfg": 7.0,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 640, "height": 640, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_PROMPT, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "triz", "images": ["8", 0]}},
    }


def generate_image(prompt_text: str, seed: int = 0, timeout: int = 90) -> bytes:
    """送出生圖請求並輪詢到完成，回傳 PNG bytes。ComfyUI 不可用或逾時會丟例外。"""
    workflow = _build_workflow(prompt_text, seed)
    data = json.dumps({"prompt": workflow}).encode()
    req = urllib.request.Request(
        f"{COMFY_URL}/prompt", data=data, headers={"Content-Type": "application/json"}
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    prompt_id = resp["prompt_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            hist = json.loads(
                urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=10).read()
            )
        except urllib.error.URLError:
            hist = {}
        entry = hist.get(prompt_id)
        if entry and entry.get("outputs"):
            images = entry["outputs"].get("9", {}).get("images", [])
            if images:
                img = images[0]
                params = urllib.parse.urlencode(
                    {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
                )
                return urllib.request.urlopen(f"{COMFY_URL}/view?{params}", timeout=20).read()
            raise RuntimeError("ComfyUI 完成但沒有輸出圖片")
        time.sleep(2)
    raise TimeoutError(f"ComfyUI 生圖逾時（{timeout}秒）")
