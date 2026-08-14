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


def _python_for(project_root: Path, project_python: str | None) -> str:
    if project_python:
        return project_python
    from .runner import detect_project_python

    return detect_project_python(project_root) or sys.executable


def coverage_available(project_python: str | None = None) -> bool:
    """True if `coverage` is importable by the interpreter that runs the tests.

    `project_python` (the project's own venv interpreter) is checked via a real
    import so the answer matches what the coverage subprocess will actually do.
    """
    if project_python:
        try:
            rc = subprocess.run(
                [project_python, "-c", "import coverage"],
                capture_output=True,
                timeout=15,
            ).returncode
            return rc == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return _COVERAGE_OK


def covered_lines_for_test(
    project_root: Path,
    test_file: Path,
    timeout: int = 300,
    source_dirs: list[str] | None = None,
    project_python: str | None = None,
) -> dict[Path, set[int]]:
    """Run `coverage run -m pytest <test_file>` and return file → executed line numbers.

    Paths are relative to project_root.
    """
    py = _python_for(project_root, project_python)
    if not coverage_available(py):
        return {}
    with tempfile.TemporaryDirectory(prefix="mutegate-cov-") as td:
        data_file = Path(td) / "cov.db"
        json_file = Path(td) / "cov.json"
        cmd = [
            py,
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
            subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            subprocess.run(
                [
                    py,
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
                encoding="utf-8",
                errors="replace",
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


def _coverage_task(
    project_root_str: str,
    test_path_str: str,
    timeout: int,
    project_python: str | None = None,
) -> tuple[str, dict]:
    cov = covered_lines_for_test(
        Path(project_root_str),
        Path(test_path_str),
        timeout=timeout,
        project_python=project_python,
    )
    return test_path_str, cov


def collect_per_file_coverage(
    project_root: Path,
    test_files: list[Path],
    timeout: int = 300,
    workers: int = 4,
    project_python: str | None = None,
) -> dict[Path, set[Path]]:
    """Map each source file → set of test files that execute lines in it.

    Runs the coverage pipeline once per test file (in parallel across worker
    processes). Returns paths relative to project_root.
    """
    if not coverage_available(project_python):
        return {}
    if not test_files:
        return {}
    from concurrent.futures import ProcessPoolExecutor

    project_root = project_root.resolve()

    results: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(_coverage_task, str(project_root), str(tf), timeout, project_python)
            for tf in test_files
        ]
        for f in futures:
            test_path, cov = f.result()
            results[test_path] = cov

    source_to_tests: dict[Path, set[Path]] = {}
    for test_path, cov in results.items():
        test_rel = Path(test_path).relative_to(project_root)
        for src_rel, lines in cov.items():
            if lines:
                source_to_tests.setdefault(src_rel, set()).add(test_rel)
    return source_to_tests


def covered_lines_for_suite(project_root: Path, timeout: int = 300, project_python: str | None = None) -> dict[Path, set[int]]:
    """Run the full test suite under coverage; return file → executed lines."""
    py = _python_for(project_root, project_python)
    if not coverage_available(py):
        return {}
    with tempfile.TemporaryDirectory(prefix="mutegate-cov-") as td:
        data_file = Path(td) / "cov.db"
        json_file = Path(td) / "cov.json"
        cmd = [
            py,
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
            subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            subprocess.run(
                [
                    py,
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
                encoding="utf-8",
                errors="replace",
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
