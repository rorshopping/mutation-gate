"""Tests for report rendering and JUnit XML output."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mutation_gate.generate import generate_mutants
from mutation_gate.model import MutantResult, Report
from mutation_gate.report import junit_report, render_report, report_to_dict


def _result(mutant, status: str):
    return MutantResult(mutant=mutant, status=status, duration=1.0)


def _report() -> Report:
    m1 = generate_mutants("def f(a):\n    return a < 5\n", Path("m.py"))
    killed = [_result(m, "killed") for m in m1[:2]]
    survived = [_result(m1[2], "survived")] if len(m1) > 2 else []
    return Report(results=killed + survived, cached=3)


def test_report_score():
    r = _report()
    assert r.killed == 2
    assert r.survived == 1
    assert abs(r.score - 2 / 3) < 1e-9


def test_render_report_shows_cache():
    out = render_report(_report())
    assert "Cache hits: 3" in out


def test_report_to_dict_includes_cached():
    d = report_to_dict(_report())
    assert d["cached"] == 3
    assert d["killed"] == 2


def test_junit_xml_well_formed():
    xml = junit_report(_report())
    root = ET.fromstring(xml)
    assert root.tag == "testsuite"
    assert root.get("failures") == "1"
    testcases = [c for c in root.iter("testcase")]
    assert testcases
    survived_cases = [c for c in testcases if c.find("failure") is not None]
    assert len(survived_cases) == 1


def test_render_baseline_failed():
    r = Report(results=[], baseline_failed=True, baseline_output="boom")
    out = render_report(r)
    assert "Baseline test run FAILED" in out
