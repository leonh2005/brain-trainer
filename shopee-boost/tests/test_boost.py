# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import boost  # noqa: E402


def test_is_boost_countdown_true_for_hhmmss():
    assert boost._is_boost_countdown('03:59:35')
    assert boost._is_boost_countdown('  01:02:03  ')


def test_is_boost_countdown_false_for_prompt_or_empty():
    assert not boost._is_boost_countdown('點我置頂推廣')
    assert not boost._is_boost_countdown('')
    assert not boost._is_boost_countdown(None)
