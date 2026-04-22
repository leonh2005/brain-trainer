import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from kelly import raw_kelly, adjusted_kelly, position_sizes

def test_raw_kelly_classic():
    # 勝率 0.6, 賠率 2.0 → (0.6*2 - 0.4)/2 = 0.4
    assert abs(raw_kelly(0.6, 2.0) - 0.4) < 1e-9

def test_raw_kelly_no_edge():
    # 勝率 0.5, 賠率 1.0 → (0.5*1 - 0.5)/1 = 0
    assert raw_kelly(0.5, 1.0) == 0.0

def test_adjusted_kelly_neutral():
    # cycle_multiplier=1.0, fib_multiplier=1.0 → same as raw
    assert abs(adjusted_kelly(0.6, 2.0, 1.0, 1.0) - 0.4) < 1e-9

def test_adjusted_kelly_with_multipliers():
    # 0.4 * 1.2 * 0.8 = 0.384
    assert abs(adjusted_kelly(0.6, 2.0, 1.2, 0.8) - 0.384) < 1e-9

def test_position_sizes():
    sizes = position_sizes(capital=100000, kelly_pct=0.25)
    assert sizes['full_kelly'] == 25000.0
    assert sizes['half_kelly'] == 12500.0
