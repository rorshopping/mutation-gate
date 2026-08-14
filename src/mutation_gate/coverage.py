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


def _coverage_task(project_root_str: str, test_path_str: str, timeout: int) -> tuple[str, dict]:
    cov = covered_lines_for_test(Path(project_root_str), Path(test_path_str), timeout=timeout)
    return test_path_str, cov


def collect_per_file_coverage(
    project_root: Path,
    test_files: list[Path],
    timeout: int = 300,
    workers: int = 4,
) -> dict[Path, set[Path]]:
    """Map each source file → set of test files that execute lines in it.

    Runs the coverage pipeline once per test file (in parallel across worker
    processes). Returns paths relative to project_root.
    """
    if not coverage_available():
        return {}
    if not test_files:
        return {}
    from concurrent.futures import ProcessPoolExecutor

    project_root = project_root.resolve()

    results: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(_coverage_task, str(project_root), str(tf), timeout)
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
