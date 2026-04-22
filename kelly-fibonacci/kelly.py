def raw_kelly(win_rate: float, odds: float) -> float:
    """Kelly fraction: (win_rate * odds - lose_rate) / odds"""
    lose_rate = 1.0 - win_rate
    return (win_rate * odds - lose_rate) / odds

def adjusted_kelly(win_rate: float, odds: float, cycle_multiplier: float, fib_multiplier: float) -> float:
    """Apply cycle resonance and fibonacci entry multipliers to raw Kelly."""
    return raw_kelly(win_rate, odds) * cycle_multiplier * fib_multiplier

def position_sizes(capital: float, kelly_pct: float) -> dict:
    """Return full and half Kelly position sizes in currency units."""
    full = round(capital * kelly_pct, 2)
    return {'full_kelly': full, 'half_kelly': round(full * 0.5, 2)}
