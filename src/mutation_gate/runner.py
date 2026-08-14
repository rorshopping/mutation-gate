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


def _resolve_cmd(parts: list[str]) -> list[str]:
    """Resolve the executable, wrapping .cmd/.bat via cmd.exe on Windows.

    CreateProcess cannot exec npm.cmd directly (WinError 2 / 193); shutil.which
    resolves it via PATHEXT, and .cmd/.bat shims must run through cmd /c.
    """
    resolved = shutil.which(parts[0])
    if not resolved:
        return parts
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", *parts]
    if resolved.lower() != parts[0].lower():
        return [resolved, *parts[1:]]
    return parts


def resolve_test_cmd(command: str) -> list[str]:
    """Turn a test command string into an executable argv (worker-safe).

    `pytest` → `[python, -m, pytest, ...]`; `python`/`python3` pass through;
    anything else (npm, node, ...) is resolved via PATH (cmd.exe shim on Windows).
    """
    parts = shlex.split(command)
    if not parts:
        return [sys.executable, "-m", "pytest"]
    if parts[0] == "pytest":
        return [sys.executable, "-m", "pytest", *parts[1:]]
    if parts[0] in ("python", "python3"):
        return parts
    return _resolve_cmd(parts)


def subset_prefix(test_cmd: list[str]) -> list[str]:
    """Command prefix for running a reduced set of test files per mutant.

    Python/pytest → `python -m pytest -q <files>`; anything else (JS/TS via
    node or npm) → `node --test <files>`.
    """
    if len(test_cmd) >= 2 and test_cmd[0] in (sys.executable, "python", "python3") and test_cmd[1] == "-m":
        return [test_cmd[0], "-m", "pytest", "-q"]
    node = shutil.which("node")
    if node:
        return [node, "--test"]
    return test_cmd


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
            encoding="utf-8",
            errors="replace",
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
    # Safety: never write outside the worktree (absolute paths would resolve
    # to the real project — see the historical corruption bug).
    if not str(src.resolve()).startswith(str(workroot.resolve())):
        raise ValueError(f"mutant file escapes worktree: {mutant.file}")
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
    subset_files: list[str] | None = None,
    subset_prefix: list[str] | None = None,
) -> tuple[int, MutantResult]:
    work = _ensure_worktree(Path(project_root), Path(pool_dir))
    if subset_files:
        prefix = subset_prefix if subset_prefix is not None else [sys.executable, "-m", "pytest", "-q"]
        test_cmd = [*prefix, *subset_files]
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
        return resolve_test_cmd(self.test_command)

    def _subset_prefix(self, test_cmd: list[str]) -> list[str]:
        return subset_prefix(test_cmd)

    def baseline(self) -> tuple[bool, str]:
        """Run suite unmuted; True if baseline passes (exit 0)."""
        with tempfile.TemporaryDirectory(prefix="mutegate-base-") as td:
            work = Path(td) / "work"
            _copy_worktree(self.project_root, work)
            exit_code, _, output, _ = _run_process(self._test_cmd(), work, self.timeout)
        return exit_code == 0, output

    def run(self, mutants: list[Mutant], progress=None, subsets: dict[int, list[str]] | None = None) -> tuple[list[MutantResult], int]:
        """Run the suite against each mutant.

        `subsets` maps a mutant's index to a list of test file paths (relative
        to the project root) that cover the mutated source file. Mutants with
        a subset run only those tests; others run the full suite.

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
        if self.cache_file is not None and subsets is None:
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
                subset_prefix = self._subset_prefix(test_cmd) if subsets is not None else None
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
                            subsets.get(i) if subsets else None,
                            subset_prefix,
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

    def run_distributed(
        self,
        server_url: str,
        token: str,
        mutants: list[Mutant],
        progress=None,
        subsets: dict[int, list[str]] | None = None,
    ) -> tuple[list[MutantResult], int]:
        """Run the suite against each mutant via a distributed broker.

        Mutants are serialized into a job on the broker; workers (see
        distributed.run_worker_loop) pull tasks and execute them against
        their own checkout. Cache replay works exactly as in `run`; caching
        is disabled when per-mutant subsets are used.
        """
        from .distributed import poll_job, post_results, submit_job

        if not mutants:
            return [], 0

        results: list[MutantResult] = [None] * len(mutants)  # type: ignore[list-item]
        cached_entries: dict[int, dict] = {}

        fp = None
        cache_results: dict[str, dict] = {}
        if self.cache_file is not None and subsets is None:
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
            tasks: dict[str, dict] = {}
            for i in pending:
                m = mutants[i]
                tasks[str(i)] = {
                    "source": m.source,
                    "file": m.file.as_posix(),
                    "lineno": m.lineno,
                    "operator": m.operator,
                    "before": m.before,
                    "after": m.after,
                    "subset_files": subsets.get(i) if subsets else None,
                }

            job_id = submit_job(server_url, token, tasks, self.test_command, self.timeout)
            if progress:
                progress(len(mutants) - len(pending), len(mutants))
            remote = poll_job(
                server_url, job_id, total_timeout=self.timeout * max(1, len(pending)) + 120
            )

            for idx_str, r in remote.items():
                i = int(idx_str)
                results[i] = MutantResult(
                    mutant=mutants[i],
                    status=r.get("status", "survived"),
                    exit_code=r.get("exit_code"),
                    duration=r.get("duration", 0.0),
                    output=r.get("output", ""),
                    timed_out=bool(r.get("timed_out")),
                )

            missing = [i for i in pending if results[i] is None]
            if missing:
                raise RuntimeError(
                    f"{len(missing)} mutant(s) never completed remotely (job {job_id}); "
                    "a worker may have died — check `mutation-gate worker` logs"
                )

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
