"""Theater tests for demo.core — they run, but prove almost nothing.

This is the AI-written-test failure mode: the code executes (high coverage)
but a broken implementation would still pass these tests.
"""

from demo.core import average, clamp, count_above, describe, is_even


def test_clamp_runs():
    result = clamp(5, 0, 10)
    assert result is not None


def test_average_runs():
    result = average([1, 2, 3, 4])
    assert result is not None


def test_is_even_runs():
    result = is_even(4)
    assert result is not None


def test_count_above_runs():
    result = count_above([1, 2, 3, 4, 5], 3)
    assert result is not None


def test_describe_runs():
    result = describe(5)
    assert result is not None
