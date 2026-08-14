"""Unit tests for score computation and gate logic."""

from pathlib import Path

from mutation_gate.gate import should_pass
from mutation_gate.model import Mutant, MutantResult, Report


def _mut(id_, status):
    m = Mutant(id=id_, file=Path("x.py"), lineno=1, operator="num_literal",
               before="1", after="2", source="", original="")
    return MutantResult(mutant=m, status=status)


def test_score_computation():
    r = Report(results=[_mut(0, "killed"), _mut(1, "killed"), _mut(2, "survived"), _mut(3, "invalid")])
    assert r.killed == 2
    assert r.survived == 1
    assert r.total_counted == 3
    assert r.score == 2 / 3


def test_score_empty():
    r = Report(results=[])
    assert r.score is None
    assert not should_pass(r, 0.5)


def test_gate_pass_fail():
    good = Report(results=[_mut(0, "killed"), _mut(1, "killed"), _mut(2, "killed")])
    bad = Report(results=[_mut(0, "killed"), _mut(1, "survived"), _mut(2, "survived")])
    assert should_pass(good, 0.5)
    assert not should_pass(bad, 0.5)


def test_baseline_failed_always_fails():
    r = Report(results=[_mut(0, "killed")], baseline_failed=True)
    assert not should_pass(r, 0.0)
