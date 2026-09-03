# -*- coding: utf-8 -*-
"""TRIZ 解題助手（本機版）：矛盾矩陣查表 + Groq 生成建議 + ComfyUI 生成漫畫風示意圖。"""

import json
import os
import re
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from openai import OpenAI

import comfy_client
import db

load_dotenv()

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, "static", "images")

TRIZ_DATA = json.load(open(os.path.join(BASE_DIR, "triz_data.json"), encoding="utf-8"))
PARAMS = TRIZ_DATA["params"]
PRINCIPLES = TRIZ_DATA["principles"]
MATRIX = TRIZ_DATA["matrix"]
PRINCIPLE_BY_ID = {p["id"]: p for p in PRINCIPLES}
PARAM_BY_ID = {p["id"]: p for p in PARAMS}

GROQ_MODEL = "openai/gpt-oss-120b"


def groq_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"```[a-z]*\n?", "", text.strip()).strip("`").strip()
    return json.loads(cleaned)


def lookup_principles(improving: int, worsening: int) -> list:
    if improving == worsening:
        return []
    return MATRIX.get(f"{improving},{worsening}", [])


def analyze_problem(problem: str) -> dict:
    """用 Groq 判斷問題對應的矛盾矩陣參數。"""
    param_list = "\n".join(f"{p['id']}. {p['zh']}" for p in PARAMS)
    prompt = (
        f"你是 TRIZ（發明問題解決理論）專家。以下是 39 個工程參數清單：\n{param_list}\n\n"
        f"使用者的問題：「{problem}」\n\n"
        "請判斷這個問題對應矛盾矩陣的哪兩個參數：\n"
        "- improving：使用者想要改善/提升的參數編號\n"
        "- worsening：因此會惡化/變差的參數編號\n\n"
        "只能用清單裡的編號（1-39），且兩者不能相同。"
        '用 JSON 回答，格式：{"improving": 數字, "worsening": 數字, "reasoning": "一句話中文說明"}'
    )
    resp = groq_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=600,
    )
    content = resp.choices[0].message.content
    if not content:
        raise RuntimeError(f"AI 沒有回傳內容（finish_reason={resp.choices[0].finish_reason}），請重試")
    result = _parse_json(content)
    improving, worsening = int(result["improving"]), int(result["worsening"])
    if not (1 <= improving <= 39 and 1 <= worsening <= 39) or improving == worsening:
        raise ValueError("AI 判斷的參數超出範圍或相同")
    return {"improving": improving, "worsening": worsening, "reasoning": result.get("reasoning", "")}


def generate_suggestions(problem: str, improving: int, worsening: int) -> list:
    """用 Groq 生成 2-4 個具體建議，每個附漫畫插圖生成 prompt。"""
    principle_ids = lookup_principles(improving, worsening)
    if principle_ids:
        principle_text = "\n".join(
            f"{PRINCIPLE_BY_ID[pid]['zh']}：{PRINCIPLE_BY_ID[pid]['idea']}" for pid in principle_ids
        )
    else:
        principle_text = "（此矛盾組合無經典對應原理，請直接根據問題脈絡與 TRIZ 精神提出建議）"
    imp_name = f"{improving}.{PARAM_BY_ID[improving]['zh']}"
    wor_name = f"{worsening}.{PARAM_BY_ID[worsening]['zh']}"

    prompt = (
        f"你是 TRIZ 顧問。使用者的問題情境：「{problem or '(未提供文字描述，僅提供矛盾參數)'}」\n\n"
        f"矛盾參數：改善「{imp_name}」時會惡化「{wor_name}」\n\n"
        f"對應的 TRIZ 發明原理：\n{principle_text}\n\n"
        "請針對使用者的具體情境，提出 2-4 個具體可執行的建議（盡量一個發明原理對應一個建議；"
        "即使問題偏抽象，也要轉化成一個具體可畫出來的物理/機構層面改動）。每個建議包含：\n"
        "- text：30-60字的中文說明，具體到「在物品的哪裡加什麼、改什麼」\n"
        "- image_prompt：一句英文的圖像生成提示詞，描述這個物品改動後的樣子，"
        "風格固定加上 'comic book illustration, bold black ink outlines, flat cel shading, "
        "clean vector-like style, white background'，具體描述物品本體＋新增/改變的部件\n\n"
        '用 JSON 回答，格式：{"suggestions": [{"text":"...","image_prompt":"..."}, ...]}'
    )
    resp = groq_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000,
    )
    content = resp.choices[0].message.content
    if not content:
        raise RuntimeError(f"AI 沒有回傳內容（finish_reason={resp.choices[0].finish_reason}），請重試")
    result = _parse_json(content)
    suggestions = result.get("suggestions", [])[:4]

    comfy_ok = comfy_client.is_available()
    for i, s in enumerate(suggestions):
        s["image_url"] = None
        s["image_error"] = None
        if not comfy_ok:
            s["image_error"] = "ComfyUI 未啟動（外接硬碟需插著並手動開 ComfyUI）"
            continue
        try:
            png_bytes = comfy_client.generate_image(s.get("image_prompt", ""), seed=int(time.time()) + i)
            filename = f"{int(time.time() * 1000)}_{i}.png"
            with open(os.path.join(IMAGES_DIR, filename), "wb") as f:
                f.write(png_bytes)
            s["image_url"] = f"/static/images/{filename}"
        except Exception as e:
            s["image_error"] = str(e)

    return suggestions, principle_ids


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "templates/index.html")


@app.get("/api/data")
def api_data():
    return jsonify({"params": PARAMS, "principles": PRINCIPLES})


@app.get("/api/lookup")
def api_lookup():
    improving = request.args.get("improving", type=int)
    worsening = request.args.get("worsening", type=int)
    if improving is None or worsening is None:
        return jsonify({"error": "缺少 improving/worsening"}), 400
    principle_ids = lookup_principles(improving, worsening)
    return jsonify({"principle_ids": principle_ids})


@app.post("/api/analyze")
def api_analyze():
    problem = (request.json or {}).get("problem", "").strip()
    if not problem:
        return jsonify({"error": "請輸入問題"}), 400
    try:
        result = analyze_problem(problem)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/suggest")
def api_suggest():
    body = request.json or {}
    problem = body.get("problem", "").strip()
    improving = body.get("improving")
    worsening = body.get("worsening")
    if improving is None or worsening is None:
        return jsonify({"error": "缺少 improving/worsening"}), 400
    try:
        suggestions, principle_ids = generate_suggestions(problem, int(improving), int(worsening))
        db.add_history(problem, int(improving), int(worsening), principle_ids, suggestions)
        return jsonify({"suggestions": suggestions, "principle_ids": principle_ids})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/history")
def api_history():
    return jsonify({"history": db.list_history()})


@app.get("/api/comfy-status")
def api_comfy_status():
    return jsonify({"available": comfy_client.is_available()})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5980))
    app.run(host="0.0.0.0", port=port, debug=False)
