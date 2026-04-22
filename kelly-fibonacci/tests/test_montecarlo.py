import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np
from montecarlo import run_simulation, simulation_stats

def test_simulation_shape():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    assert curves.shape == (100, 51)  # 100 paths, 51 columns (initial + 50 trades)

def test_simulation_initial_capital():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    assert np.all(curves[:, 0] == 100000)

def test_stats_keys():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=200, seed=42)
    stats = simulation_stats(curves, initial_capital=100000)
    for key in ['p5', 'p50', 'p95', 'max_drawdown_mean', 'max_drawdown_std',
                'max_drawdown_worst', 'ruin_rate']:
        assert key in stats

def test_ruin_rate_type():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    stats = simulation_stats(curves, initial_capital=100000)
    assert 0.0 <= stats['ruin_rate'] <= 1.0
