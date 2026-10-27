"""test_classify_day: classify_day(row, range_p30) 的十大類型判斷。

覆蓋所有 10 種走勢類型的觸發條件，以及邊界情況（rng=0、十字線、窄幅）。
"""

from analyze import classify_day


def _row(o, h, l, c):
    """Build a dict mimicking a DataFrame row with Open/High/Low/Close."""
    return {"Open": o, "High": h, "Low": l, "Close": c}


# ── 十字線（實體佔比 < 10%）──────────────────────────────────────

def test_doji_is_classified_as_9():
    """實體極小（< 10% of range）→ ⑨十字變盤，優先於所有其他判斷。"""
    # O=100 H=110 L=90 C=100.1 → body=0.1, range=20, b=0.005 < 0.10
    row = _row(100, 110, 90, 100.1)
    assert classify_day(row, range_p30=5) == "⑨十字變盤"


def test_doji_takes_priority_over_narrow():
    """即使 range < range_p30，十字線仍優先分類為 ⑨。"""
    row = _row(100, 102, 98, 100.05)  # body=0.05, range=4, b=0.0125
    assert classify_day(row, range_p30=10) == "⑨十字變盤"


# ── 窄幅震盪 ─────────────────────────────────────────────────────

def test_narrow_range_red():
    """range < range_p30 且收紅 → ⑦窄幅紅盤震盪。"""
    # body=0.5, range=4, b=0.125 > 0.10 → not doji; range=4 < range_p30=10
    row = _row(100, 102, 98, 100.5)
    assert classify_day(row, range_p30=10) == "⑦窄幅紅盤震盪"


def test_narrow_range_black():
    """range < range_p30 且收黑 → ⑧窄幅黑盤震盪。"""
    row = _row(100, 102, 98, 99.5)  # body=-0.5
    assert classify_day(row, range_p30=10) == "⑧窄幅黑盤震盪"


# ── 長影線 ───────────────────────────────────────────────────────

def test_long_upper_shadow():
    """上影線 > 35% of range 且 > 下影線 → ⑤衝高回落。

    注意：實體 b < 0.10 會先被判為十字（優先序最高），所以資料必須有實體。
    O=100 H=125 L=95 C=104 → rng=30, body=4, b=0.133（非十字）
    upper=21(0.70 因 H 高), lower=4(0.133) → up_ratio > 0.35 且 > dn_ratio
    """
    row = _row(100, 125, 95, 104)
    assert classify_day(row, range_p30=10) == "⑤衝高回落(長上影)"


def test_doji_takes_priority_over_long_shadow():
    """實體為 0 時，即使上影線很長也先判十字（優先序：十字 > 長影）。"""
    # O=100 H=120 L=90 C=100 → body=0, b=0 → doji，即使 up_ratio=0.667
    row = _row(100, 120, 90, 100)
    assert classify_day(row, range_p30=10) == "⑨十字變盤"


def test_long_lower_shadow():
    """下影線 > 35% of range 且 > 上影線 → ⑥探底回升。"""
    # O=100 H=110 L=80 C=105 → upper=5, lower=25, range=30
    # dn_ratio=25/30=0.833 > 0.35
    row = _row(100, 110, 80, 105)
    assert classify_day(row, range_p30=10) == "⑥探底回升(長下影)"


# ── 收極端（趨勢單邊日）────────────────────────────────────────

def test_extreme_close_high():
    """pos >= 0.95 → ⑩趨勢單邊日。

    需要兩側影線都窄，否則會先撞到長影規則：
    O=92 H=110 L=90 C=109 → rng=20, body=17, b=0.85
    upper=1(0.05), lower=2(0.10) → 都不超過 0.35，才輪到 pos 判斷
    """
    row = _row(92, 110, 90, 109)
    assert classify_day(row, range_p30=10) == "⑩趨勢單邊日(收極端)"


def test_extreme_close_low():
    """pos <= 0.05 → ⑩趨勢單邊日。同樣需要兩側影線都窄。"""
    # O=108 H=110 L=90 C=91 → rng=20, body=-17, b=0.85
    # upper=2(0.10), lower=1(0.05) → 都窄，才輪到 pos 判斷
    row = _row(108, 110, 90, 91)
    assert classify_day(row, range_p30=10) == "⑩趨勢單邊日(收極端)"


def test_long_lower_shadow_wins_over_extreme_close():
    """下影線夠長時，即使 pos >= 0.95 也先判 ⑥（優先序：長影 > 極端）。"""
    # O=100 H=110 L=90 C=109 → pos=0.95 但 lower=10(0.5) → ⑥
    row = _row(100, 110, 90, 109)
    assert classify_day(row, range_p30=10) == "⑥探底回升(長下影)"


# ── 開低走高 / 開高走高 ─────────────────────────────────────────

def test_open_low_close_high_strong():
    """收紅、pos > 0.8、開盤偏下 → ①開低走高強多。"""
    # O=95 H=110 L=90 C=108 → red, pos=18/20=0.9, mid=(110+90)/2=100, O=95<100
    row = _row(95, 110, 90, 108)
    assert classify_day(row, range_p30=10) == "①開低走高強多"


def test_open_low_close_high_strong():
    """收紅、pos > 0.8、開盤在中價以下、兩側影線都窄 → ①開低走高強多。"""
    # O=95 H=110 L=90 C=108 → rng=20, body=13, b=0.65
    # upper=2(0.10), lower=5(0.25) → 都 <= 0.35
    # pos=(108-90)/20=0.9, mid=100, O=95 <= 100 → ①開低走高強多
    row = _row(95, 110, 90, 108)
    assert classify_day(row, range_p30=10) == "①開低走高強多"


def test_open_high_close_high_unreachable():
    """`①開高走高強多` 這個分支實際上不可達，記錄現況。

    app 的 else 分支要求 o > mid，但 o > mid 且 pos > 0.8 時，
    lower_shadow = o - l >= rng/2，dn_ratio 必 >= 0.5 > 0.35，
    因此會先被 ⑥探底回升攔下。這個測試固定目前行為，若日後修正
    分類邏輯（例如放寬長影門檻）就會失敗，屆時應一併更新。
    """
    # O=105 H=110 L=90 C=108 → dn_ratio=0.75 > 0.35 → ⑥
    row = _row(105, 110, 90, 108)
    assert classify_day(row, range_p30=10) == "⑥探底回升(長下影)"


# ── 開高走低 ────────────────────────────────────────────────────

def test_open_high_close_low():
    """收黑、pos >= 0.2（非極端）→ ③開高走低翻黑。"""
    # O=105 H=110 L=90 C=95 → red=False, pos=(95-90)/20=0.25, not < 0.2
    row = _row(105, 110, 90, 95)
    assert classify_day(row, range_p30=10) == "③開高走低翻黑"


# ── 開低走低 ────────────────────────────────────────────────────

def test_open_low_close_low_weak():
    """收黑、pos < 0.2、兩側影線都不超過 35% → ④開低走低弱空。

    ④ 的可達區間很窄，四個條件必須同時成立：
      O=103 H=110 L=90 C=93 → rng=20（>= range_p30 不會變窄幅）
      b=0.5（非十字）、up_ratio=0.35（不 > 0.35 所以不觸發 ⑤）
      dn_ratio=0.15（不 > 0.35 所以不觸發 ⑥）、pos=0.15 < 0.2
    """
    row = _row(103, 110, 90, 93)
    assert classify_day(row, range_p30=10) == "④開低走低弱空"


def test_long_upper_shadow_wins_over_weak_short():
    """上影線過長時優先判 ⑤，不會落到 ④。"""
    # O=98 H=110 L=90 C=93 → up_ratio=0.6 > 0.35 → ⑤
    row = _row(98, 110, 90, 93)
    assert classify_day(row, range_p30=10) == "⑤衝高回落(長上影)"


def test_open_high_open_low_close_low():
    """收黑、pos < 0.2、開盤偏高 → ④開低走低弱空（第二分支）。"""
    # 生產程式的 if 判斷是 o >= mid 才走這個分支
    # O=105 H=110 L=90 C=92 → red=False, pos=(92-90)/20=0.1, mid=100, O=105>=100
    row = _row(105, 110, 90, 92)
    assert classify_day(row, range_p30=10) == "④開低走低弱空"


# ── 邊界情況 ────────────────────────────────────────────────────

def test_zero_range():
    """High == Low → rng=0 → 窄幅震盪(其他)。"""
    row = _row(100, 100, 100, 100)
    assert classify_day(row, range_p30=10) == "窄幅震盪(其他)"


def test_negative_range_is_treated_as_zero():
    """生產程式用 rng <= 0 判斷（理論上不會發生但防衛性檢查）。"""
    # 實際上 yfinance 不會回 H < L，但 classify_day 用 rng <= 0 做防衛
    row = _row(100, 99, 100, 100)  # H=99 < L=100 → rng=-1
    assert classify_day(row, range_p30=10) == "窄幅震盪(其他)"
