"""gate: CI exit-code gate on mutation score."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .model import Report


def should_pass(report: Report, min_score: float) -> bool:
    if report.baseline_failed:
        return False
    if report.score is None:
        return False
    return report.score >= min_score
