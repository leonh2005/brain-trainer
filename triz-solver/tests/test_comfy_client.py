# -*- coding: utf-8 -*-
"""comfy_client.py 純邏輯測試：只測 _build_workflow（不碰網路）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfy_client import CHECKPOINT, NEGATIVE_PROMPT, _build_workflow


def test_build_workflow_contains_all_required_nodes():
    """workflow 必須包含 8 個必要節點（3-9）才能跑完整生圖管線。"""
    wf = _build_workflow("test prompt", seed=42)
    assert set(wf.keys()) == {"3", "4", "5", "6", "7", "8", "9"}


def test_build_workflow_sets_seed():
    """seed 要正確傳進 KSampler。"""
    wf = _build_workflow("x", seed=12345)
    assert wf["3"]["inputs"]["seed"] == 12345


def test_build_workflow_sets_prompt_text():
    """正向 prompt 要放進 node 6 的 text。"""
    wf = _build_workflow("a cute robot", seed=1)
    assert wf["6"]["inputs"]["text"] == "a cute robot"


def test_build_workflow_uses_fixed_negative_prompt():
    """反向 prompt 必須是模組常數，不隨使用者輸入改變。"""
    wf = _build_workflow("anything", seed=1)
    assert wf["7"]["inputs"]["text"] == NEGATIVE_PROMPT


def test_build_workflow_uses_configured_checkpoint():
    """checkpoint 要與模組常數一致。"""
    wf = _build_workflow("x", seed=1)
    assert wf["4"]["inputs"]["ckpt_name"] == CHECKPOINT


def test_build_workflow_640x640_output():
    """輸出尺寸固定 640x640。"""
    wf = _build_workflow("x", seed=1)
    assert wf["5"]["inputs"]["width"] == 640
    assert wf["5"]["inputs"]["height"] == 640


def test_build_workflow_ksampler_wiring():
    """KSampler 要正確串接 model/positive/negative/latent。"""
    wf = _build_workflow("x", seed=1)
    ks = wf["3"]["inputs"]
    assert ks["model"] == ["4", 0]
    assert ks["positive"] == ["6", 0]
    assert ks["negative"] == ["7", 0]
    assert ks["latent_image"] == ["5", 0]


def test_build_workflow_save_image_prefix():
    """SaveImage 的 filename_prefix 固定為 triz。"""
    wf = _build_workflow("x", seed=1)
    assert wf["9"]["inputs"]["filename_prefix"] == "triz"
