from learn_system.mastery import compute_status


def attempts(*verdicts):
    """最新在前"""
    return [{"verdict": v} for v in verdicts]


def test_no_attempts_is_untested():
    assert compute_status([]) == "untested"


def test_all_correct_but_fewer_than_five_is_practicing():
    assert compute_status(attempts(*["correct"] * 4)) == "practicing"


def test_five_correct_is_mastered():
    assert compute_status(attempts(*["correct"] * 5)) == "mastered"


def test_four_of_five_correct_is_mastered():
    assert compute_status(attempts("wrong", "correct", "correct", "correct", "correct")) == "mastered"


def test_three_of_five_correct_is_practicing():
    assert compute_status(attempts("wrong", "wrong", "correct", "correct", "correct")) == "practicing"


def test_partial_does_not_count_as_correct():
    assert compute_status(attempts("partial", "partial", "correct", "correct", "correct")) == "practicing"


def test_four_correct_plus_one_partial_is_mastered():
    assert compute_status(attempts("partial", "correct", "correct", "correct", "correct")) == "mastered"


def test_only_last_five_attempts_matter():
    # 最早那筆錯誤已被擠出窗口
    old_wrong = attempts("correct", "correct", "correct", "correct", "correct", "wrong")
    assert compute_status(old_wrong) == "mastered"


def test_mastered_can_regress():
    assert compute_status(attempts("wrong", "wrong", "correct", "correct", "correct", "correct", "correct")) == "practicing"
