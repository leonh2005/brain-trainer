FIB_LEVELS = {
    23.6: {'entry_multiplier': 0.6, 'reward_ratio': 1.5},
    38.2: {'entry_multiplier': 0.8, 'reward_ratio': 2.0},
    50.0: {'entry_multiplier': 0.9, 'reward_ratio': 2.5},
    61.8: {'entry_multiplier': 1.0, 'reward_ratio': 3.0},
    78.6: {'entry_multiplier': 1.1, 'reward_ratio': 4.0},
}

def get_fib_params(level: float) -> dict:
    return FIB_LEVELS[level]
