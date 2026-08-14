"""Data models shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Mutant:
    """A single mutated variant of a source file."""

    id: int
    file: Path  # path relative to project root
    lineno: int
    operator: str
    before: str  # unparsed original node
    after: str  # unparsed mutated node
    source: str  # full mutated file content
    original: str  # full original file content


@dataclass
class MutantResult:
    """Outcome of running the test suite against one mutant."""

    mutant: Mutant
    status: str  # "killed" | "survived" | "invalid"
    exit_code: int | None = None
    duration: float = 0.0
    output: str = ""
    timed_out: bool = False


@dataclass
class Report:
    """Aggregated mutation run results."""

    results: list[MutantResult]
    baseline_failed: bool = False
    baseline_output: str = ""
    config_source: str = "defaults"
    cached: int = 0

    @property
    def counted(self) -> list[MutantResult]:
        return [r for r in self.results if r.status in ("killed", "survived")]

    @property
    def killed(self) -> int:
        return sum(1 for r in self.counted if r.status == "killed")

    @property
    def survived(self) -> int:
        return sum(1 for r in self.counted if r.status == "survived")

    def surviving(self) -> list["MutantResult"]:
        return [r for r in self.counted if r.status == "survived"]

    @property
    def invalid(self) -> int:
        return sum(1 for r in self.results if r.status == "invalid")

    @property
    def total_counted(self) -> int:
        return len(self.counted)

    @property
    def score(self) -> float | None:
        if self.total_counted == 0:
            return None
        return self.killed / self.total_counted


@dataclass
class VerifyResult:
    """Contribution of a single test file to mutation killing."""

    test_file: Path
    covered_files: dict[Path, set[int]] = field(default_factory=dict)
    reachable: int = 0
    killed: int = 0
    survived: int = 0
    invalid: int = 0
    survivors: list[Mutant] = field(default_factory=list)
    coverage_available: bool = True

    @property
    def contribution(self) -> float | None:
        if self.reachable == 0:
            return None
        return self.killed / self.reachable
