"""Demo module: a small library with real behavior for mutation testing."""

from __future__ import annotations


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def average(numbers: list[float]) -> float:
    """Arithmetic mean; returns 0.0 for empty list."""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


def is_even(n: int) -> bool:
    """True when n is even."""
    return n % 2 == 0


def count_above(numbers: list[float], threshold: float) -> int:
    """Count how many values exceed threshold."""
    count = 0
    for n in numbers:
        if n > threshold:
            count += 1
    return count


def describe(n: int) -> str:
    """Human label for a number."""
    if n == 0:
        return "zero"
    if n < 0:
        return "negative"
    return "positive"
