"""V 型反轉三階段偵測的單元測試。

判定定義（Steven 2026-11-06 指定，1 分 K、嚴格門檻、失效不推播）：
  A 疑似止跌：跌破近 N 根低點、長下影線、收盤位置偏高、量能增加
  B 初步反轉：收盤站回 VWAP、突破 A 段高點、量能擴大
  C 回測確認：回踩 VWAP 附近守住、低點高於 A 段低點、再突破 B 段高點
"""

import pandas as pd
from vshape import VShapeParams, VShapeState, detect_v_shape


def make_df(rows: list[tuple[float, float, float, float, int]]) -> pd.DataFrame:
    """rows: (open, high, low, close, volume) → 1 分 K DataFrame。"""
    ts = pd.date_range("2026-11-06 09:00", periods=len(rows), freq="1min")
    return pd.DataFrame(
        [{"ts": t, "open": o, "high": h, "low": lo, "close": c, "volume": v}
         for t, (o, h, lo, c, v) in zip(ts, rows)]
    )


def flat_bars(n: int = 20, price: float = 100.0, volume: int = 1000) -> list:
    """n 根橫盤 K 棒，低點 99.5（供 A 階段跌破用）。"""
    return [(price, price + 0.2, price - 0.5, price + 0.1, volume) for _ in range(n)]


# A 觸發棒：open 99.8 high 100.0 low 99.0 close 99.9 vol 2500
#   下影線 (99.8-99.0)/1.0 = 80% ≥ 60%
#   收盤位置 (99.9-99.0)/1.0 = 90% ≥ 75%
#   low 99.0 < 前 20 根最低 99.5
A_BAR = (99.8, 100.0, 99.0, 99.9, 2500)
# B 棒：站上 VWAP 且突破 A 高點 100.0，量 3000 ≥ 2×1300
B_BAR = (99.9, 101.0, 99.9, 100.8, 3000)
# C 回踩棒：low 100.0 貼近 VWAP、close 100.3 站穩、低點高於 A 低點 99.0
C_PULLBACK_BAR = (100.5, 100.6, 100.0, 100.3, 1200)
# C 突破棒：close 101.2 > B 段高點 101.0
C_BREAKOUT_BAR = (100.4, 101.5, 100.4, 101.2, 1500)


def feed(df: pd.DataFrame, state: VShapeState, params: VShapeParams) -> list:
    """逐根餵入，模擬盤中每分鐘呼叫一次，回傳所有新成立的事件。"""
    events = []
    for i in range(1, len(df) + 1):
        event = detect_v_shape(df.iloc[:i], state, params)
        if event is not None:
            events.append(event)
    return events


def test_a_stage_triggers_on_breakdown_with_long_lower_shadow():
    df = make_df(flat_bars() + [A_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert len(events) == 1
    assert events[0].stage == "A"
    assert state.stage == "A"
    assert state.a_low == 99.0
    assert state.a_high == 100.0


def test_a_stage_rejects_shallow_lower_shadow():
    # 下影線 (99.6-99.4)/1.0 = 20% < 60%
    bar = (99.8, 100.4, 99.4, 99.6, 2500)
    df = make_df(flat_bars() + [bar])
    state = VShapeState()

    assert feed(df, state, VShapeParams()) == []
    assert state.stage == "idle"


def test_a_stage_rejects_weak_close_position():
    # 長下影線但收在區間下半部：close 99.3 位置 (99.3-99.0)/(100.7-99.0)=18%
    bar = (100.5, 100.7, 99.0, 99.3, 2500)
    df = make_df(flat_bars() + [bar])
    state = VShapeState()

    assert feed(df, state, VShapeParams()) == []
    assert state.stage == "idle"


def test_a_stage_rejects_insufficient_volume():
    # 形狀合格但量只有 1200 < 2×1000
    bar = (99.8, 100.0, 99.0, 99.9, 1200)
    df = make_df(flat_bars() + [bar])
    state = VShapeState()

    assert feed(df, state, VShapeParams()) == []
    assert state.stage == "idle"


def test_a_stage_requires_low_below_recent_low():
    # 形狀與量都合格，但 low 99.6 沒有跌破前 20 根低點 99.5
    bar = (100.4, 100.9, 99.6, 100.8, 2500)
    df = make_df(flat_bars() + [bar])
    state = VShapeState()

    assert feed(df, state, VShapeParams()) == []
    assert state.stage == "idle"


def test_a_stage_needs_minimum_bars():
    # 資料只有 5 根，不足 lookback + 1
    df = make_df(flat_bars(n=4) + [A_BAR])
    state = VShapeState()

    assert feed(df, state, VShapeParams()) == []
    assert state.stage == "idle"


def test_b_stage_triggers_after_a_on_vwap_reclaim_and_a_high_break():
    df = make_df(flat_bars() + [A_BAR, B_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "B"]
    assert state.stage == "B"
    assert state.b_high == 101.0


def test_b_stage_rejects_when_close_below_vwap():
    # 突破 A 高點但收盤仍在 VWAP 之下（大量收在 100.05，VWAP 約 99.99）
    bar = (99.9, 100.9, 99.9, 100.02, 3000)
    df = make_df(flat_bars() + [A_BAR, bar])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A"]
    assert state.stage == "A"


def test_b_stage_rejects_insufficient_volume():
    # 站上 VWAP 且破 A 高，但量 1500 < 2×1300=2600
    bar = (99.9, 101.0, 99.9, 100.8, 1500)
    df = make_df(flat_bars() + [A_BAR, bar])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A"]
    assert state.stage == "A"


def test_c_stage_triggers_after_pullback_to_vwap_then_breakout():
    df = make_df(flat_bars() + [A_BAR, B_BAR, C_PULLBACK_BAR, C_BREAKOUT_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "B", "C"]
    assert state.stage == "C"


def test_c_stage_requires_pullback_before_breakout():
    # 直接突破 B 高點、沒有先回踩 VWAP → 不成立 C
    bar = (100.9, 101.8, 100.9, 101.5, 1500)
    df = make_df(flat_bars() + [A_BAR, B_BAR, bar])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "B"]
    assert state.stage == "B"


def test_c_stage_rejects_pullback_that_breaks_a_low():
    # 回踩但 low 98.8 跌破 A 段低點 99.0 → 回踩不合格，後續突破也不成 C
    pullback = (99.5, 99.6, 98.8, 99.4, 1200)
    df = make_df(flat_bars() + [A_BAR, B_BAR, pullback, C_BREAKOUT_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert "C" not in [e.stage for e in events]


def test_a_stage_invalidates_silently_when_new_low_breaks():
    # A 成立後跌破 a_low：狀態重置，不產生事件
    breakdown = (99.2, 99.3, 98.5, 98.8, 1500)
    df = make_df(flat_bars() + [A_BAR, breakdown])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A"]
    assert state.stage == "idle"


def test_b_stage_invalidates_after_three_closes_below_vwap():
    # B 成立後連續 3 根收盤跌破 VWAP → 重置（VWAP 約 100.18）
    bars = [
        (100.2, 100.3, 99.9, 100.0, 1200),
        (100.0, 100.1, 99.8, 99.9, 1200),
        (99.9, 100.0, 99.7, 99.8, 1200),
    ]
    df = make_df(flat_bars() + [A_BAR, B_BAR] + bars)
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "B"]
    assert state.stage == "idle"


def test_same_stage_never_pushes_twice():
    # 事件只在「新成立」時回傳；同一根 K 棒重複呼叫不會再觸發
    df = make_df(flat_bars() + [A_BAR])
    state = VShapeState()
    params = VShapeParams()

    first = detect_v_shape(df, state, params)
    second = detect_v_shape(df, state, params)

    assert first is not None and first.stage == "A"
    assert second is None


def test_restored_pushed_stage_is_not_reannounced():
    """模擬異常重啟：pushed 已記住同一根 A，重新判定時只重建狀態、不再推播。"""
    df = make_df(flat_bars() + [A_BAR])
    state = VShapeState()
    state.pushed["A"] = "09:20"   # 第 21 根 = 09:20

    events = feed(df, state, VShapeParams())

    assert events == []
    assert state.stage == "A", "狀態仍要建立，後續 B/C 才能接續判定"


def test_second_setup_same_day_can_push_again():
    """同一檔同一天：第一輪 A 破底失效後，新的 A 型態仍應再推一次。"""
    breakdown = (99.2, 99.3, 98.5, 98.8, 1500)          # 跌破 a_low 99.0 → 重置
    quiet = [(98.6, 98.8, 98.2, 98.7, 1000)] * 20        # 新橫盤，低點 98.2
    second_a = (98.5, 98.6, 97.9, 98.55, 2500)           # 跌破 98.2、下影線 86%、量 2.5x
    df = make_df(flat_bars() + [A_BAR, breakdown] + quiet + [second_a])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "A"]
    assert events[0].ts != events[1].ts


def test_a_stage_expires_when_b_never_comes():
    """A 成立後 A 逾時根數內沒進 B，狀態回 idle，讓新的止跌型態可以重新判定。"""
    chop = [(99.5, 99.6, 99.4, 99.55, 500)] * 32
    df = make_df(flat_bars() + [A_BAR] + chop)
    state = VShapeState()

    feed(df, state, VShapeParams())

    assert state.stage == "idle"


def test_c_stage_rejects_pullback_far_below_vwap():
    """回踩必須落在 VWAP 附近；跌破下容差視為沒守住，不算回踩。"""
    deep = (100.0, 100.1, 99.2, 99.3, 1200)   # VWAP 約 100.2，低點低 1%
    df = make_df(flat_bars() + [A_BAR, B_BAR, deep, C_BREAKOUT_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert "C" not in [e.stage for e in events]


def test_c_stage_needs_breakout_on_a_later_bar_than_pullback():
    """同一根不能同時算回踩與突破：C 要求先回踩、下一根再突破。"""
    both = (99.9, 101.5, 100.0, 101.2, 1500)   # 低點碰 VWAP、收盤已破 b_high
    df = make_df(flat_bars() + [A_BAR, B_BAR, both])
    state = VShapeState()

    events = feed(df, state, VShapeParams())

    assert [e.stage for e in events] == ["A", "B"]
    assert state.pullback_seen is True, "這一根已登記為回踩，等下一根才算突破"


def test_replay_state_rebuilds_stage_without_emitting():
    """重啟回放：狀態要回到當時的階段，且不產生任何事件。"""
    from vshape import replay_state

    df = make_df(flat_bars() + [A_BAR, B_BAR])
    state = VShapeState()

    replay_state(df, state, VShapeParams())

    assert state.stage == "B"
    assert state.pushed == {}


def test_detect_skips_unclosed_bar_by_row_count():
    """同一批資料重複呼叫不會再判定（呼叫端負責只餵已收盤的 K 棒）。"""
    df = make_df(flat_bars() + [A_BAR])
    state = VShapeState()
    params = VShapeParams()

    assert detect_v_shape(df, state, params) is not None
    assert detect_v_shape(df, state, params) is None


def test_event_carries_price_vwap_and_detail():
    df = make_df(flat_bars() + [A_BAR])
    state = VShapeState()

    events = feed(df, state, VShapeParams())
    event = events[0]

    assert event.price == 99.9
    assert event.vwap > 0
    assert "lower_shadow_pct" in event.detail
    assert 80 <= event.detail["lower_shadow_pct"] <= 81
    assert "close_pos_pct" in event.detail
    assert "volume_ratio" in event.detail
    assert "recent_low" in event.detail
