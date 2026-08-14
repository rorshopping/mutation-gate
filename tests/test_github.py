from __future__ import annotations

import os
from pathlib import Path

from mutation_gate.github import detect_pr, render_pr_comment
from mutation_gate.model import Mutant, MutantResult, Report


def _sample_report() -> Report:
    m1 = Mutant(id=0, file=Path("src/demo/core.py"), lineno=8, operator="comparison", before="value < lo", after="value <= lo", source="x", original="y")
    m2 = Mutant(id=1, file=Path("src/demo/core.py"), lineno=40, operator="num_literal", before="0", after="1", source="x", original="y")
    return Report(
        results=[
            MutantResult(mutant=m1, status="survived", exit_code=0, duration=0.5, output=""),
            MutantResult(mutant=m2, status="survived", exit_code=0, duration=0.4, output=""),
            MutantResult(mutant=m1, status="killed", exit_code=1, duration=0.3, output=""),
        ],
        config_source="pyproject.toml",
        cached=10,
    )


def test_render_pr_comment_contains_score_and_table():
    body = render_pr_comment(_sample_report(), min_score=0.8)
    assert "**Mutation score: 33.3%**" in body
    assert "(1/3 mutants killed)" in body
    assert "Cache hits: 10" in body
    assert "❌ **FAIL**" in body
    assert "`src/demo/core.py` | 8 |" in body
    assert "| 40 | `num_literal`" in body


def test_render_pr_comment_all_killed():
    m = Mutant(id=0, file=Path("src/a.py"), lineno=1, operator="binop", before="a + b", after="a - b", source="x", original="y")
    body = render_pr_comment(Report(results=[MutantResult(mutant=m, status="killed")], config_source=".mutation-gate.toml"))
    assert "All mutants killed" in body
    assert "surviving mutants" not in body


def test_render_pr_comment_baseline_failed():
    body = render_pr_comment(Report(results=[], baseline_failed=True, baseline_output="boom"))
    assert "Baseline test run failed" in body


def test_render_pr_comment_pipe_escaping():
    m = Mutant(id=0, file=Path("src/a.py"), lineno=1, operator="str_literal", before='"a|b"', after='"MUTANT"', source="x", original="y")
    body = render_pr_comment(Report(results=[MutantResult(mutant=m, status="survived")], config_source="cfg"))
    assert "a\\|b" in body


def test_detect_pr_none_outside_ci(monkeypatch):
    for k in ("GITHUB_REPOSITORY", "GITHUB_EVENT_PATH", "PR_NUMBER", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert detect_pr() is None


def test_detect_pr_from_event_path(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text('{"pull_request": {"number": 42}}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert detect_pr() == ("owner/repo", 42)


def test_detect_pr_from_pr_number_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("PR_NUMBER", "7")
    assert detect_pr() == ("owner/repo", 7)
