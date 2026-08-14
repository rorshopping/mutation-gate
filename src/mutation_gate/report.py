"""Report rendering: text tables, JSON, and JUnit XML output."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .model import Report, VerifyResult


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_report(report: Report) -> str:
    if report.baseline_failed:
        return (
            "❌ Baseline test run FAILED — cannot run mutation gate.\n"
            f"Tests must pass before mutation testing. Last output:\n{report.baseline_output[:2000]}"
        )
    score = report.score
    cached = report.cached
    lines = [
        f"Mutation score: {_pct(score)}  ({report.killed}/{report.total_counted} mutants killed)",
        f"Invalid mutants: {report.invalid}   Config: {report.config_source}",
    ]
    if cached:
        lines.append(f"Cache hits: {cached} (replayed without re-running the suite)")
    lines.append("")
    if report.survived:
        lines.append("Surviving mutants:")
        for r in report.surviving():
            m = r.mutant
            lines.append(
                f"  {m.file}:{m.lineno}  [{m.operator}]  {m.before}  →  {m.after}  "
                f"({r.duration:.2f}s)"
            )
    else:
        lines.append("All mutants killed. Tests have real teeth. 🎉")
    return "\n".join(lines)


def render_verify(vr: VerifyResult, gate_score: float | None = None) -> str:
    if not vr.coverage_available:
        cov_note = "  [coverage.py not found — running against ALL mutants, no filtering]"
    else:
        cov_note = ""
    lines = [
        f"Test file: {vr.test_file}{cov_note}",
        f"Reachable mutants: {vr.reachable}   Killed by this test: {vr.killed}   "
        f"Survived: {vr.survived}   Invalid: {vr.invalid}",
        f"Contribution: {_pct(vr.contribution)}  ({vr.killed}/{vr.reachable})",
        "",
    ]
    if gate_score is not None:
        ok = vr.contribution is not None and vr.contribution >= gate_score
        lines.append(f"Gate (min {gate_score * 100:.0f}%): {'PASS' if ok else 'FAIL'}")
        lines.append("")
    elif vr.contribution is not None:
        if vr.contribution >= 0.7:
            verdict = "Strong — this test file has real teeth."
        elif vr.contribution >= 0.5:
            verdict = "Decent, but leaves a lot of behavior unproven."
        else:
            verdict = "Weak — high reach, low kill. Likely a theater test."
        lines.append(f"Verdict: {verdict}")
        lines.append("")
    if vr.survivors:
        lines.append("Surviving mutants (this test file did not catch these):")
        for m in vr.survivors:
            lines.append(f"  {m.file}:{m.lineno}  [{m.operator}]  {m.before}  →  {m.after}")
    return "\n".join(lines)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)


def report_to_dict(report: Report) -> dict:
    return {
        "score": report.score,
        "killed": report.killed,
        "survived": report.survived,
        "total": report.total_counted,
        "invalid": report.invalid,
        "cached": report.cached,
        "baseline_failed": report.baseline_failed,
        "config_source": report.config_source,
        "mutants": [
            {
                "file": str(r.mutant.file),
                "line": r.mutant.lineno,
                "operator": r.mutant.operator,
                "before": r.mutant.before,
                "after": r.mutant.after,
                "status": r.status,
                "duration_s": round(r.duration, 3),
            }
            for r in report.results
        ],
    }


def render_mutants(mutants: list) -> str:
    lines = [f"Generated {len(mutants)} mutants."]
    for m in mutants:
        lines.append(f"  {m.file}:{m.lineno}  [{m.operator}]  {m.before}  →  {m.after}")
    return "\n".join(lines)


def junit_report(report: Report) -> str:
    """Render a JUnit XML file (one testcase per mutant) for CI ingestion."""
    root = ET.Element("testsuite", name="mutation-gate")
    root.set("tests", str(report.total_counted))
    root.set("failures", str(report.survived))
    root.set("skipped", str(report.invalid))
    root.set("errors", "0")
    root.set(
        "properties",
        f"score={report.score} killed={report.killed} survived={report.survived} cached={report.cached}",
    )
    for r in report.counted:
        m = r.mutant
        case = ET.SubElement(root, "testcase")
        case.set("classname", m.file.as_posix().replace("/", "."))
        case.set("name", f"{m.operator}:{m.lineno}")
        case.set("time", f"{r.duration:.3f}")
        if r.status == "survived":
            failure = ET.SubElement(case, "failure", type="survived")
            failure.text = f"{m.before}\n→\n{m.after}"
    for r in report.results:
        if r.status == "invalid":
            case = ET.SubElement(root, "testcase", classname="invalid", name=str(r.mutant.file))
            case.set("time", "0")
            ET.SubElement(case, "skipped", type="invalid")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def verify_to_dict(vr: VerifyResult) -> dict:
    return {
        "test_file": str(vr.test_file),
        "coverage_available": vr.coverage_available,
        "reachable": vr.reachable,
        "killed": vr.killed,
        "survived": vr.survived,
        "invalid": vr.invalid,
        "contribution": vr.contribution,
        "survivors": [
            {
                "file": str(m.file),
                "line": m.lineno,
                "operator": m.operator,
                "before": m.before,
                "after": m.after,
            }
            for m in vr.survivors
        ],
    }
