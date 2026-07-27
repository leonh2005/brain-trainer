# -*- coding: utf-8 -*-
"""當前月份落點計算。

彙整「現在」在杜金龍波浪劇本的位置(已過哪一浪、距各目標與回檔買點多遠)，
以及各市場/經濟週期當前的段位。供側欄顯示。
"""

import cycles
import econ_cycles


def wave_status(script: dict, current_ym: str, current_price: float) -> dict:
    """判斷 current_ym/current_price 在波浪劇本中的位置。"""
    anchors = sorted(script["anchors"], key=lambda a: a["date"])
    passed = [a for a in anchors if a["date"] <= current_ym]
    last_anchor = passed[-1] if passed else None

    targets = [
        {**t, "gap_pct": round((t["price"] - current_price) / current_price * 100, 1)}
        for t in script.get("targets", [])
    ]
    retracements = [
        {
            **r,
            "low_gap_pct": round((r["low"] - current_price) / current_price * 100, 1),
            "high_gap_pct": round((r["high"] - current_price) / current_price * 100, 1),
        }
        for r in script.get("retracements", [])
    ]
    return {
        "script_id": script["id"],
        "script_label": script["label"],
        "source": script["source"],
        "current_ym": current_ym,
        "current_price": current_price,
        "last_passed_wave": last_anchor["wave"] if last_anchor else None,
        "last_passed_date": last_anchor["date"] if last_anchor else None,
        "targets": targets,
        "retracements": retracements,
    }


def cycle_stages(current_ym: str) -> dict:
    """所有市場與經濟週期在 current_ym 的當前段位。"""
    return {
        "market": [cycles.describe_current(current_ym, cy) for cy in cycles.DEFAULT_CYCLES],
        "econ": [econ_cycles.describe_current(current_ym, cy) for cy in econ_cycles.DEFAULT_ECON_CYCLES],
    }
