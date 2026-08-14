"""Test-suite execution against mutants, in isolated worktrees.

Each worker process keeps a single persistent worktree and mutates the file
in place between test runs, so the project is copied once per worker instead
of once per mutant. Results are cached on disk keyed by a project fingerprint
so unchanged re-runs skip the suite entirely.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import cache as cache_mod
from .model import Mutant, MutantResult

_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".mutation-gate",
}


def _copy_worktree(src: Path, dst: Path) -> None:
    dst_tmp = Path(str(dst) + ".tmp")
    shutil.rmtree(dst_tmp, ignore_errors=True)
    shutil.copytree(
        src,
        dst_tmp,
        ignore=shutil.ignore_patterns(
            *[f"{d}" for d in _IGNORE_DIRS],
            "*.pyc",
            "*.egg-info",
        ),
    )
    dst_tmp.replace(dst)


def _run_process(cmd: list[str], cwd: Path, timeout: int) -> tuple[int | None, float, str, bool]:
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        exit_code = proc.returncode
        output = proc.stdout[-4000:] + proc.stderr[-4000:]
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = None
        output = f"timed out after {timeout}s"
    duration = time.monotonic() - start
    return exit_code, duration, output, timed_out


def _ensure_worktree(project_root: Path, pool_dir: Path) -> Path:
    """Return this worker's private worktree, copying the project on first use."""
    work = Path(pool_dir) / f"w{os.getpid()}"
    if not work.exists():
        _copy_worktree(project_root, work)
    return work


def _run_one(workroot: Path, mutant: Mutant, test_cmd: list[str], timeout: int) -> MutantResult:
    """Apply one mutant in a worktree, run the suite, restore the file."""
    src = Path(workroot) / mutant.file
    orig = src.read_text(encoding="utf-8")
    try:
        src.write_text(mutant.source, encoding="utf-8")
        exit_code, duration, output, timed_out = _run_process(test_cmd, Path(workroot), timeout)
    finally:
        src.write_text(orig, encoding="utf-8")
    if (exit_code == 0 or exit_code == 5) and not timed_out:
        # exit 5 = "no tests collected": mutant not really exercised → count as survived
        status = "survived"
    else:
        status = "killed"
    return MutantResult(
        mutant=mutant,
        status=status,
        exit_code=exit_code,
        duration=duration,
        output=output,
        timed_out=timed_out,
    )


def _run_task(
    idx: int,
    mutant: Mutant,
    pool_dir: str,
    project_root: str,
    test_cmd: list[str],
    timeout: int,
) -> tuple[int, MutantResult]:
    work = _ensure_worktree(Path(project_root), Path(pool_dir))
    result = _run_one(work, mutant, test_cmd, timeout)
    return idx, result


class Runner:
    """Runs the test suite against each mutant in a private worktree."""

    def __init__(
        self,
        project_root: Path,
        test_command: str = "pytest",
        timeout: int = 60,
        workers: int = 4,
        cache_file: Path | None = None,
    ):
        self.project_root = project_root.resolve()
        self.test_command = test_command
        self.timeout = timeout
        self.workers = max(1, workers)
        self.cache_file = cache_file

    def _test_cmd(self) -> list[str]:
        parts = shlex.split(self.test_command)
        if not parts:
            return [sys.executable, "-m", "pytest"]
        if parts[0] == "pytest":
            return [sys.executable, "-m", "pytest", *parts[1:]]
        if parts[0] in ("python", "python3"):
            return parts
        return [sys.executable, "-m", *parts]

    def baseline(self) -> tuple[bool, str]:
        """Run suite unmuted; True if baseline passes (exit 0)."""
        with tempfile.TemporaryDirectory(prefix="mutegate-base-") as td:
            work = Path(td) / "work"
            _copy_worktree(self.project_root, work)
            exit_code, _, output, _ = _run_process(self._test_cmd(), work, self.timeout)
        return exit_code == 0, output

    def run(self, mutants: list[Mutant], progress=None) -> tuple[list[MutantResult], int]:
        """Run the suite against each mutant.

        Returns (results, n_cached). Cached results are replayed when the
        project fingerprint is unchanged; the suite is only executed for
        mutants with no valid cache entry.
        """
        if not mutants:
            return [], 0

        results: list[MutantResult] = [None] * len(mutants)  # type: ignore[list-item]
        cached_entries: dict[int, dict] = {}

        fp = None
        cache_results: dict[str, dict] = {}
        if self.cache_file is not None:
            fp = cache_mod.fingerprint(self.project_root, self.test_command, self.timeout)
            cache_results = cache_mod.load_results(self.cache_file, fp)

        pending: list[int] = []
        for i, m in enumerate(mutants):
            key = cache_mod.mutant_key(m.source, m.file.as_posix(), m.operator)
            hit = cache_results.get(key)
            if hit and hit.get("status") in ("killed", "survived"):
                results[i] = MutantResult(
                    mutant=m,
                    status=hit["status"],
                    exit_code=hit.get("exit_code"),
                    duration=hit.get("duration", 0.0),
                )
                cached_entries[i] = hit
            else:
                pending.append(i)

        if pending:
            base_dir = Path(tempfile.mkdtemp(prefix="mutegate-run-"))
            try:
                pool_dir = base_dir / "pool"
                pool_dir.mkdir()
                test_cmd = self._test_cmd()
                timeout = self.timeout
                done = len(mutants) - len(pending)

                with ProcessPoolExecutor(max_workers=self.workers) as pool:
                    futures = [
                        pool.submit(
                            _run_task,
                            i,
                            mutants[i],
                            str(pool_dir),
                            str(self.project_root),
                            test_cmd,
                            timeout,
                        )
                        for i in pending
                    ]
                    for f in futures:
                        i, result = f.result()
                        results[i] = result
                        done += 1
                        if progress:
                            progress(done, len(mutants))
            finally:
                shutil.rmtree(base_dir, ignore_errors=True)

            if self.cache_file is not None and fp is not None:
                for i in pending:
                    r = results[i]
                    key = cache_mod.mutant_key(
                        r.mutant.source, r.mutant.file.as_posix(), r.mutant.operator
                    )
                    cache_results[key] = {
                        "status": r.status,
                        "exit_code": r.exit_code,
                        "duration": r.duration,
                    }
                cache_mod.save_cache(self.cache_file, fp, cache_results)

        return [r for r in results if r is not None], len(cached_entries)


def filter_invalid(mutants: list[Mutant]) -> tuple[list[Mutant], list[Mutant]]:
    valid, invalid = [], []
    for m in mutants:
        try:
            compile(m.source, str(m.file), "exec")
            valid.append(m)
        except SyntaxError:
            invalid.append(m)
    return valid, invalid
