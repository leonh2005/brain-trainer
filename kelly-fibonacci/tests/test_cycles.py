import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from cycles import resonance_score, CYCLE_WEIGHTS

def test_weights_sum_to_one():
    assert abs(sum(CYCLE_WEIGHTS.values()) - 1.0) < 1e-9

def test_all_neutral_gives_one():
    score, multiplier = resonance_score(0, 0, 0, 0)
    assert score == 0.0
    assert multiplier == 1.0

def test_all_max_expansion():
    score, multiplier = resonance_score(2, 2, 2, 2)
    assert abs(score - 2.0) < 1e-9
    assert abs(multiplier - 1.5) < 1e-9

def test_all_max_contraction():
    score, multiplier = resonance_score(-2, -2, -2, -2)
    assert abs(score - (-2.0)) < 1e-9
    assert abs(multiplier - 0.5) < 1e-9

def test_weighted_mix():
    # kitchin=2(w=0.15), juglar=0(w=0.25), kuznets=0(w=0.30), kondratiev=0(w=0.30)
    score, multiplier = resonance_score(2, 0, 0, 0)
    assert abs(score - 0.30) < 1e-9
    # multiplier = 1.0 + (0.30/2.0)*0.5 = 1.075
    assert abs(multiplier - 1.075) < 1e-9
