WINDOW = 5
REQUIRED_CORRECT = 4


def compute_status(attempts: list[dict]) -> str:
    """attempts 為最近作答，最新在前。"""
    if not attempts:
        return "untested"
    recent = attempts[:WINDOW]
    correct = sum(1 for a in recent if a["verdict"] == "correct")
    if len(recent) >= WINDOW and correct >= REQUIRED_CORRECT:
        return "mastered"
    return "practicing"
