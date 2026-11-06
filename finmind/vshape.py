"""V 型反轉三階段偵測（1 分 K）。

判定定義（2026-11-06 指定）：
  A 疑似止跌：跌破近 N 根低點、長下影線、收盤位置偏高、量能增加
  B 初步反轉：收盤站回 VWAP、突破 A 段高點、量能擴大
  C 回測確認：回踩 VWAP 附近守住、低點高於 A 段低點、再突破 B 段高點

設計原則：
  - 純函式：所有狀態由呼叫端傳入的 VShapeState 持有，模組不留全域。
  - 呼叫端必須只餵「已收盤」的 K 棒；未收盤的那根量能與價位都還在變動，判定沒有意義。
  - 只回報「新成立」的階段；同一根 K 棒重複呼叫不會重複觸發。
  - pushed 以 {階段: 該階段成立的那根 K 棒時間} 記錄，同一輪不重推、新的一輪可以再推。
  - 失效（破底／站不回 VWAP／A 逾時）只重置狀態，不產生事件（依 Steven 指定）。
"""

from dataclasses import dataclass, field

import pandas as pd

MAX_BARS_BELOW_VWAP = 3  # B 階段連續幾根收盤跌破 VWAP 視為失效


@dataclass(frozen=True)
class VShapeParams:
    """可調門檻。預設為 2026-11-06 選定的「嚴格」組。"""

    lookback: int = 20          # 「近期低點」回看根數
    shadow_ratio: float = 0.60  # 下影線占全長比例下限
    close_pos: float = 0.75     # 收盤在 K 棒區間的位置下限
    vol_mult: float = 2.0       # 量能相對前 5 根均量的倍數下限
    vwap_tol_up: float = 0.002    # C 回踩 VWAP 的上容差（+0.2%）
    vwap_tol_down: float = 0.005  # C 回踩 VWAP 的下容差（-0.5%），跌破視為沒守住
    a_expiry_bars: int = 30       # A 成立後幾根內沒進 B 就失效

    @property
    def min_bars(self) -> int:
        return self.lookback + 1


@dataclass
class VShapeState:
    """單一標的的階段狀態。呼叫端持有，跨分鐘保留。"""

    stage: str = "idle"        # idle / A / B / C
    a_low: float = 0.0
    a_high: float = 0.0
    b_high: float = 0.0
    a_ts: str = ""
    b_ts: str = ""
    a_index: int = 0           # A 成立時的 K 棒數，用於逾時判定
    pullback_seen: bool = False
    pullback_low: float = 0.0
    pullback_vwap: float = 0.0
    pullback_index: int = 0
    bars_below_vwap: int = 0
    last_len: int = 0          # 已處理過的 K 棒數，避免同批資料重複判定
    pushed: dict = field(default_factory=dict)  # {stage: 成立時間}，供重啟後去重
    needs_replay: bool = False  # 重啟後第一次見到資料時，需用當日 K 棒重建階段


@dataclass(frozen=True)
class VShapeEvent:
    stage: str
    ts: str
    price: float
    vwap: float
    detail: dict


def _reset(state: VShapeState) -> None:
    """回到 idle。刻意不清 pushed：那是跨重啟的去重紀錄，不是階段狀態。"""
    state.stage = "idle"
    state.a_low = state.a_high = state.b_high = 0.0
    state.a_ts = state.b_ts = ""
    state.a_index = 0
    state.pullback_seen = False
    state.pullback_low = state.pullback_vwap = 0.0
    state.pullback_index = 0
    state.bars_below_vwap = 0


def _vwap(df: pd.DataFrame) -> float:
    """當日累積成交量加權均價，與 intraday_monitor.calc_vwap 同一定義。"""
    vol = df["volume"].astype(float)
    cum_vol = vol.cumsum().iloc[-1]
    if cum_vol <= 0:
        return float("nan")
    return float((df["close"].astype(float) * vol).cumsum().iloc[-1] / cum_vol)


def _bar_ts(df: pd.DataFrame) -> str:
    raw = df["ts"].iloc[-1]
    return raw.strftime("%H:%M") if hasattr(raw, "strftime") else str(raw)


def detect_v_shape(
    df: pd.DataFrame,
    state: VShapeState,
    params: VShapeParams | None = None,
    emit: bool = True,
) -> VShapeEvent | None:
    """檢查最新一根已收盤的 1 分 K，必要時推進階段。

    回傳新成立階段的 VShapeEvent；沒有新階段（含失效、重複呼叫、emit=False）回傳 None。
    emit=False 供重啟後回放歷史重建狀態，只推進階段、不產生事件。
    """
    params = params or VShapeParams()
    if df is None or len(df) < params.min_bars or len(df) == state.last_len:
        return None
    if not {"open", "high", "low", "close", "volume", "ts"}.issubset(df.columns):
        return None

    bar = df.iloc[-1]
    high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
    open_ = float(bar["open"])
    volume = float(bar["volume"])
    vwap = _vwap(df)
    if high <= low or pd.isna(vwap):
        return None
    state.last_len = len(df)

    rng = high - low
    lower_shadow = (min(open_, close) - low) / rng
    close_pos = (close - low) / rng
    recent_vol = df["volume"].iloc[-6:-1].mean()
    vol_ratio = volume / recent_vol if recent_vol and recent_vol > 0 else 0.0
    ts = _bar_ts(df)

    def _new(stage: str, **detail) -> VShapeEvent | None:
        """階段成立：emit=False 或同一根已推過就不回報。

        刻意不在這裡寫 pushed —— 由呼叫端在「推播成功」後才記錄，
        否則 Telegram 回 429/400 時會把沒送出的階段記成已推，永久漏掉。
        """
        if not emit or state.pushed.get(stage) == ts:
            return None
        return VShapeEvent(stage=stage, ts=ts, price=close, vwap=vwap, detail=detail)

    if state.stage == "idle":
        recent_low = float(df["low"].iloc[-(params.lookback + 1):-1].min())
        if (
            low < recent_low
            and lower_shadow >= params.shadow_ratio
            and close_pos >= params.close_pos
            and vol_ratio >= params.vol_mult
        ):
            state.stage = "A"
            state.a_low, state.a_high, state.a_ts = low, high, ts
            state.a_index = len(df)
            return _new(
                "A",
                lower_shadow_pct=round(lower_shadow * 100, 1),
                close_pos_pct=round(close_pos * 100, 1),
                volume_ratio=round(vol_ratio, 1),
                recent_low=recent_low,
                bar_low=low,
            )
        return None

    if state.stage == "A":
        if len(df) - state.a_index >= params.a_expiry_bars:
            _reset(state)   # A 拖太久沒進 B，讓新的止跌型態可以重新判定
            return None
        if low < state.a_low:
            _reset(state)
            return None
        if close > vwap and close > state.a_high and vol_ratio >= params.vol_mult:
            state.stage = "B"
            state.b_high, state.b_ts = high, ts
            return _new(
                "B",
                volume_ratio=round(vol_ratio, 1),
                a_high=state.a_high,
                a_low=state.a_low,
            )
        return None

    # stage == "B"
    if low < state.a_low:
        _reset(state)
        return None
    if close < vwap:
        state.bars_below_vwap += 1
        if state.bars_below_vwap >= MAX_BARS_BELOW_VWAP:
            _reset(state)
        return None
    state.bars_below_vwap = 0

    if (
        not state.pullback_seen
        and vwap * (1 - params.vwap_tol_down) <= low <= vwap * (1 + params.vwap_tol_up)
        and low > state.a_low
    ):
        state.pullback_seen = True
        state.pullback_low = low
        state.pullback_vwap = vwap
        state.pullback_index = len(df)

    # 突破必須晚於回踩那根：「回踩後守住、再突破」是兩段，同一根不算
    if state.pullback_seen and len(df) > state.pullback_index and close > state.b_high:
        state.stage = "C"
        return _new(
            "C",
            pullback_low=state.pullback_low,
            pullback_vwap=state.pullback_vwap,
            b_high=state.b_high,
            a_low=state.a_low,
        )
    return None


def replay_state(df: pd.DataFrame, state: VShapeState, params: VShapeParams | None = None) -> None:
    """用當日已完成的 K 棒重建階段（重啟後用），過程中不產生任何事件。"""
    params = params or VShapeParams()
    saved_pushed = dict(state.pushed)
    _reset(state)
    state.last_len = 0
    for i in range(1, len(df) + 1):
        detect_v_shape(df.iloc[:i], state, params, emit=False)
    state.pushed = saved_pushed
