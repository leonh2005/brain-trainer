# -*- coding: utf-8 -*-
"""杜金龍艾略特波浪劇本：載入與驗證。

劇本是手動維護的參數(data/wave_scripts.json)，每個版本含：
- anchors: 波浪轉折點(年月 + 點位 + 來源說明)
- targets: 目標價位(水平線)
- retracements: 回檔買點區間

重要：這些是杜金龍個人主觀判斷、會隨行情更新。圖上務必標註來源與日期。
"""

import json
import os

_SCRIPTS_PATH = os.path.join(os.path.dirname(__file__), "data", "wave_scripts.json")

_REQUIRED_SCRIPT_KEYS = {"id", "label", "source", "anchors", "targets"}
_REQUIRED_ANCHOR_KEYS = {"wave", "date", "price"}


class WaveScriptError(Exception):
    """劇本檔格式錯誤。"""


def load_scripts(path: str | None = None) -> list[dict]:
    """載入並驗證所有劇本版本。"""
    with open(path or _SCRIPTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    scripts = data.get("scripts")
    if not isinstance(scripts, list) or not scripts:
        raise WaveScriptError("劇本檔缺少非空的 scripts 陣列")
    for s in scripts:
        _validate_script(s)
    return scripts


def get_script(script_id: str, path: str | None = None) -> dict:
    """取指定 id 的劇本，找不到拋錯。"""
    for s in load_scripts(path):
        if s["id"] == script_id:
            return s
    raise WaveScriptError(f"找不到劇本版本: {script_id}")


def list_script_ids(path: str | None = None) -> list[dict]:
    """回傳 [{id, label}]，供前端下拉選單用。"""
    return [{"id": s["id"], "label": s["label"]} for s in load_scripts(path)]


def _validate_script(s: dict) -> None:
    missing = _REQUIRED_SCRIPT_KEYS - s.keys()
    if missing:
        raise WaveScriptError(f"劇本 {s.get('id', '?')} 缺少欄位: {missing}")
    if not s["anchors"]:
        raise WaveScriptError(f"劇本 {s['id']} 的 anchors 不可為空")
    prev_ym = None
    for a in s["anchors"]:
        amissing = _REQUIRED_ANCHOR_KEYS - a.keys()
        if amissing:
            raise WaveScriptError(f"劇本 {s['id']} 有 anchor 缺欄位: {amissing}")
        _parse_ym(a["date"], s["id"])  # 驗證日期格式 YYYY-MM
        if not isinstance(a["price"], (int, float)) or a["price"] <= 0:
            raise WaveScriptError(f"劇本 {s['id']} anchor 點位不合理: {a['price']}")
        ym = a["date"]
        if prev_ym is not None and ym < prev_ym:
            raise WaveScriptError(f"劇本 {s['id']} anchor 日期非遞增: {prev_ym} → {ym}")
        prev_ym = ym


def _parse_ym(ym: str, script_id: str) -> tuple[int, int]:
    try:
        y, m = ym.split("-")
        yi, mi = int(y), int(m)
        if not (1 <= mi <= 12):
            raise ValueError
        return yi, mi
    except (ValueError, AttributeError):
        raise WaveScriptError(f"劇本 {script_id} 日期格式須為 YYYY-MM: {ym!r}")
