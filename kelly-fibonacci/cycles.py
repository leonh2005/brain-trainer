CYCLE_WEIGHTS = {
    'kitchin': 0.15,
    'juglar': 0.25,
    'kuznets': 0.30,
    'kondratiev': 0.30,
}

def resonance_score(kitchin: float, juglar: float, kuznets: float, kondratiev: float) -> tuple[float, float]:
    """
    Returns (weighted_score, position_multiplier).
    Inputs range -2 to +2. Multiplier maps [-2,+2] → [0.5, 1.5].
    """
    score = (
        kitchin * CYCLE_WEIGHTS['kitchin'] +
        juglar * CYCLE_WEIGHTS['juglar'] +
        kuznets * CYCLE_WEIGHTS['kuznets'] +
        kondratiev * CYCLE_WEIGHTS['kondratiev']
    )
    multiplier = 1.0 + (score / 2.0) * 0.5
    return round(score, 4), round(multiplier, 4)
