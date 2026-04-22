import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fibonacci import get_fib_params, FIB_LEVELS

def test_fib_levels_keys():
    assert set(FIB_LEVELS.keys()) == {23.6, 38.2, 50.0, 61.8, 78.6}

def test_get_fib_params_38():
    params = get_fib_params(38.2)
    assert params['entry_multiplier'] == 0.8
    assert params['reward_ratio'] == 2.0

def test_get_fib_params_61():
    params = get_fib_params(61.8)
    assert params['entry_multiplier'] == 1.0
    assert params['reward_ratio'] == 3.0

def test_deeper_retracement_higher_multiplier():
    m_38 = get_fib_params(38.2)['entry_multiplier']
    m_61 = get_fib_params(61.8)['entry_multiplier']
    assert m_61 > m_38
