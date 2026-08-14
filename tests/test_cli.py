"""Integration test: run the full pipeline on a tiny inline project."""

import re
import subprocess
import sys
import textwrap
from pathlib import Path


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "core.py").write_text(
        textwrap.dedent(
            """\
            def clamp(v, lo, hi):
                if v < lo:
                    return lo
                if v > hi:
                    return hi
                return v
            """
        )
    )
    (root / "tests" / "test_real.py").write_text(
        textwrap.dedent(
            """\
            import sys
            sys.path.insert(0, "src")
            from core import clamp

            def test_lower():
                assert clamp(0, 1, 5) == 1
            def test_upper():
                assert clamp(9, 1, 5) == 5
            def test_mid():
                assert clamp(3, 1, 5) == 3
            """
        )
    )
    (root / "pyproject.toml").write_text(
        "[tool.mutation-gate]\ntest_command = \"pytest\"\nworkers = 2\ninclude_globs = [\"src/**/*.py\"]\n"
    )
    return root


def test_full_pipeline_run(tmp_path):
    root = _make_project(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "run", str(root), "--min-score", "0.6"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mutation score" in proc.stdout


def test_theater_test_low_contribution(tmp_path):
    root = _make_project(tmp_path)
    (root / "tests" / "test_theater.py").write_text(
        textwrap.dedent(
            """\
            import sys
            sys.path.insert(0, "src")
            from core import clamp

            def test_clamp_runs():
                result = clamp(3, 1, 5)
                assert result is not None
            """
        )
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "verify", str(root / "tests" / "test_theater.py"), str(root)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Contribution" in proc.stdout
    assert "n/a" not in proc.stdout  # coverage should be available (pytest+coverage installed)


def test_cache_replay_shows_hits(tmp_path):
    root = _make_project(tmp_path)
    args = [sys.executable, "-m", "mutation_gate.cli", "run", str(root)]
    p1 = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert p1.returncode == 0, p1.stdout + p1.stderr
    p2 = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert p2.returncode == 0, p2.stdout + p2.stderr
    match = re.search(r"Cache hits: (\d+)", p2.stdout)
    assert match is not None
    assert int(match.group(1)) > 0


def test_no_cache_flag(tmp_path):
    root = _make_project(tmp_path)
    args = [sys.executable, "-m", "mutation_gate.cli", "run", str(root), "--no-cache"]
    p1 = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert p1.returncode == 0
    p2 = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert p2.returncode == 0
    assert "Cache hits:" not in p2.stdout


def test_mutate_dump(tmp_path):
    root = _make_project(tmp_path)
    out = tmp_path / "dump"
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "mutate", str(root), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (out / "manifest.json").exists()
    assert len(list(out.glob("mutant-*.py"))) > 0


def test_junit_output(tmp_path):
    root = _make_project(tmp_path)
    junit = tmp_path / "junit.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "run", str(root), "--junit", str(junit)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert junit.exists()
    import xml.etree.ElementTree as ET

    tree = ET.parse(str(junit))
    assert tree.getroot().tag == "testsuite"


def test_operators_filter(tmp_path):
    root = _make_project(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "run", str(root), "--operators", "comparison"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "comparison" in proc.stdout or "killed" in proc.stdout


def test_json_output(tmp_path):
    root = _make_project(tmp_path)
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", "run", str(root), "--json", str(out)],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "score" in data
    assert "mutants" in data

