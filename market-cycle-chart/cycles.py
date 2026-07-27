# -*- coding: utf-8 -*-
"""市場週期正弦波產生器與相位判定。

每條週期由兩個參數決定：
- period_months: 週期長度(月)
- trough_ym: 谷底錨點(YYYY-MM)，此月為正弦波最低點

正弦波定義：value(t) = -cos(2π * months_since_trough / period)
  在谷底(months=0)：-cos(0) = -1 ✓
  半週期後：-cos(π) = +1（頂部）✓

相位 phase = (months_since_trough / period) mod 1，範圍 [0,1)：
  0.00 谷底 → 0.25 上升中 → 0.50 頂部 → 0.75 下降中 → (1.0 回到谷底)
"""

import math

# 預設市場週期定義(可於介面微調 period_months 與 trough_ym)
# 多空大循環用杜金龍講的 89 個月大循環：2022-10 底 → 半週期後 ≈2026 年中為頂，符合實際高點
DEFAULT_CYCLES = [
    {"key": "bull_bear", "name": "台股多空大循環(杜金龍89月)", "period_months": 89, "trough_ym": "2022-10"},
    {"key": "silicon", "name": "半導體/科技庫存循環", "period_months": 42, "trough_ym": "2020-04"},
    {"key": "seasonal", "name": "季節性/月份效應", "period_months": 12, "trough_ym": "2020-08"},
]


def months_between(from_ym: str, to_ym: str) -> int:
    """to_ym 減 from_ym 的月數差(可為負)。"""
    fy, fm = _parse(from_ym)
    ty, tm = _parse(to_ym)
    return (ty - fy) * 12 + (tm - fm)


def sine_value(target_ym: str, period_months: float, trough_ym: str) -> float:
    """單一月份的正弦波值，範圍 [-1, 1]。"""
    n = months_between(trough_ym, target_ym)
    return -math.cos(2 * math.pi * n / period_months)


def sine_series(months: list[str], period_months: float, trough_ym: str) -> list[float]:
    """一串月份的正弦波值。"""
    return [round(sine_value(ym, period_months, trough_ym), 4) for ym in months]


def phase_at(target_ym: str, period_months: float, trough_ym: str) -> float:
    """相位 [0,1)：0=谷底 0.25=上升 0.5=頂 0.75=下降。"""
    n = months_between(trough_ym, target_ym)
    return (n / period_months) % 1.0


def classify_phase(phase: float) -> str:
    """相位 → 人話段位。頂/底各佔 ±1/8 週期。"""
    p = phase % 1.0
    if p < 0.125 or p >= 0.875:
        return "底部"
    if p < 0.375:
        return "上升段"
    if p < 0.625:
        return "頂部"
    return "下降段"


def describe_current(target_ym: str, cycle: dict) -> dict:
    """回傳某週期在 target_ym 的當前狀態描述。"""
    phase = phase_at(target_ym, cycle["period_months"], cycle["trough_ym"])
    return {
        "key": cycle["key"],
        "name": cycle["name"],
        "phase": round(phase, 3),
        "stage": classify_phase(phase),
        "value": round(sine_value(target_ym, cycle["period_months"], cycle["trough_ym"]), 3),
    }


def _parse(ym: str) -> tuple[int, int]:
    y, m = ym.split("-")
    return int(y), int(m)
