"""Real tests for demo.core — strong assertions, high mutation score."""

from demo.core import average, clamp, count_above, describe, is_even


def test_clamp_bounds():
    assert clamp(5, 0, 10) == 5
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10


def test_clamp_edge_equal():
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10


def test_average_basic():
    assert average([1, 2, 3, 4]) == 2.5


def test_average_empty():
    assert average([]) == 0.0


def test_is_even():
    assert is_even(0) is True
    assert is_even(1) is False
    assert is_even(10) is True


def test_count_above():
    assert count_above([1, 2, 3, 4, 5], 3) == 2
    assert count_above([], 1) == 0


def test_describe():
    assert describe(0) == "zero"
    assert describe(-1) == "negative"
    assert describe(5) == "positive"
