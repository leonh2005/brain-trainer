import numpy as np

def run_simulation(
    win_rate: float,
    kelly_fraction: float,
    initial_capital: float,
    n_trades: int = 200,
    n_simulations: int = 1000,
    seed: int = None
) -> np.ndarray:
    """
    Returns equity curves of shape (n_simulations, n_trades + 1).
    Column 0 is initial_capital; each subsequent column is capital after that trade.
    Each win multiplies capital by (1 + kelly_fraction); each loss by (1 - kelly_fraction).
    """
    rng = np.random.default_rng(seed)
    wins = rng.random((n_simulations, n_trades)) < win_rate
    factors = np.where(wins, 1.0 + kelly_fraction, 1.0 - kelly_fraction)
    cumulative = np.cumprod(factors, axis=1)
    equity = initial_capital * cumulative
    initial_col = np.full((n_simulations, 1), initial_capital)
    return np.hstack([initial_col, equity])

def simulation_stats(curves: np.ndarray, initial_capital: float) -> dict:
    """Compute summary statistics from equity curves."""
    final_values = curves[:, -1]
    p5 = float(np.percentile(final_values, 5))
    p50 = float(np.percentile(final_values, 50))
    p95 = float(np.percentile(final_values, 95))

    # Max drawdown per path: peak-to-trough
    running_max = np.maximum.accumulate(curves, axis=1)
    drawdowns = (running_max - curves) / running_max
    max_drawdowns = np.max(drawdowns, axis=1)

    ruin_threshold = initial_capital * 0.5
    ruin_rate = float(np.mean(final_values < ruin_threshold))

    return {
        'p5': round(p5, 2),
        'p50': round(p50, 2),
        'p95': round(p95, 2),
        'max_drawdown_mean': round(float(np.mean(max_drawdowns)) * 100, 2),
        'max_drawdown_std': round(float(np.std(max_drawdowns)) * 100, 2),
        'max_drawdown_worst': round(float(np.max(max_drawdowns)) * 100, 2),
        'ruin_rate': round(ruin_rate, 4),
    }
