# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wave_scripts as ws


def test_load_real_scripts():
    scripts = ws.load_scripts()
    ids = [s["id"] for s in scripts]
    assert "v1" in ids and "v0" in ids


def test_get_script_v1_has_expected_anchors():
    v1 = ws.get_script("v1")
    prices = [a["price"] for a in v1["anchors"]]
    assert 12629 in prices and 24416 in prices and 35579 in prices
    assert any(t["price"] == 47000 for t in v1["targets"])


def test_get_script_unknown_raises():
    with pytest.raises(ws.WaveScriptError):
        ws.get_script("nonexistent")


def test_list_script_ids():
    items = ws.list_script_ids()
    assert all("id" in i and "label" in i for i in items)


def _write(tmp_path, obj):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_validate_rejects_missing_keys(tmp_path):
    bad = {"scripts": [{"id": "x", "label": "l"}]}  # 缺 source/anchors/targets
    with pytest.raises(ws.WaveScriptError):
        ws.load_scripts(_write(tmp_path, bad))


def test_validate_rejects_bad_date(tmp_path):
    bad = {"scripts": [{
        "id": "x", "label": "l", "source": "s",
        "anchors": [{"wave": "w", "date": "2026-13", "price": 100}],
        "targets": [],
    }]}
    with pytest.raises(ws.WaveScriptError):
        ws.load_scripts(_write(tmp_path, bad))


def test_validate_rejects_non_increasing_dates(tmp_path):
    bad = {"scripts": [{
        "id": "x", "label": "l", "source": "s",
        "anchors": [
            {"wave": "a", "date": "2026-05", "price": 100},
            {"wave": "b", "date": "2026-01", "price": 200},
        ],
        "targets": [],
    }]}
    with pytest.raises(ws.WaveScriptError):
        ws.load_scripts(_write(tmp_path, bad))


def test_validate_rejects_negative_price(tmp_path):
    bad = {"scripts": [{
        "id": "x", "label": "l", "source": "s",
        "anchors": [{"wave": "w", "date": "2026-01", "price": -5}],
        "targets": [],
    }]}
    with pytest.raises(ws.WaveScriptError):
        ws.load_scripts(_write(tmp_path, bad))
