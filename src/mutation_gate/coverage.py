"""Coverage analysis: which source lines does a given test file actually execute?

Uses `coverage.py` if available; otherwise returns empty (no filtering).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_COVERAGE_OK = importlib.util.find_spec("coverage") is not None


def coverage_available() -> bool:
    return _COVERAGE_OK


def covered_lines_for_test(
    project_root: Path,
    test_file: Path,
    timeout: int = 300,
    source_dirs: list[str] | None = None,
) -> dict[Path, set[int]]:
    """Run `coverage run -m pytest <test_file>` and return file → executed line numbers.

    Paths are relative to project_root.
    """
    if not coverage_available():
        return {}
    with tempfile.TemporaryDirectory(prefix="mutegate-cov-") as td:
        data_file = Path(td) / "cov.db"
        json_file = Path(td) / "cov.json"
        cmd = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--data-file={data_file}",
            f"--source={project_root}",
            "-m",
            "pytest",
            str(test_file),
            "-q",
            "--no-header",
        ]
        try:
            subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=timeout)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "json",
                    f"--data-file={data_file}",
                    "-o",
                    str(json_file),
                ],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return {}

    result: dict[Path, set[int]] = {}
    for path, info in data.get("files", {}).items():
        p = Path(path)
        try:
            rel = p.relative_to(project_root)
        except ValueError:
            rel = p
        lines = {int(ln) for ln in info.get("executed_lines", [])}
        if lines:
            result[rel] = lines
    return result


def covered_lines_for_suite(project_root: Path, timeout: int = 300) -> dict[Path, set[int]]:
    """Run the full test suite under coverage; return file → executed lines."""
    if not coverage_available():
        return {}
    with tempfile.TemporaryDirectory(prefix="mutegate-cov-") as td:
        data_file = Path(td) / "cov.db"
        json_file = Path(td) / "cov.json"
        cmd = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--data-file={data_file}",
            f"--source={project_root}",
            "-m",
            "pytest",
            "-q",
            "--no-header",
        ]
        try:
            subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=timeout)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "json",
                    f"--data-file={data_file}",
                    "-o",
                    str(json_file),
                ],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            return {}

    result: dict[Path, set[int]] = {}
    for path, info in data.get("files", {}).items():
        p = Path(path)
        try:
            rel = p.relative_to(project_root)
        except ValueError:
            rel = p
        lines = {int(ln) for ln in info.get("executed_lines", [])}
        if lines:
            result[rel] = lines
    return result
