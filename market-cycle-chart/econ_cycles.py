# -*- coding: utf-8 -*-
"""經濟週期背景參考層(基欽/朱格拉)。

與市場週期用同一套正弦波機制(見 cycles.py)，只是不同預設參數，
且前端以更淡的顏色、更細的線呈現、預設關閉——當「基本面節奏」參考。
"""

import cycles

# 基欽：存貨週期約 40 個月；朱格拉：設備投資週期約 108 個月(9年)
# 谷底錨點為示意近期低點，可於介面微調
DEFAULT_ECON_CYCLES = [
    {"key": "kitchin", "name": "基欽週期(存貨)", "period_months": 40, "trough_ym": "2023-01"},
    {"key": "juglar", "name": "朱格拉週期(設備投資)", "period_months": 108, "trough_ym": "2020-03"},
]


def series(months: list[str], cycle: dict) -> list[float]:
    """某經濟週期在一串月份的正弦波值(複用 cycles)。"""
    return cycles.sine_series(months, cycle["period_months"], cycle["trough_ym"])


def describe_current(target_ym: str, cycle: dict) -> dict:
    return cycles.describe_current(target_ym, cycle)
