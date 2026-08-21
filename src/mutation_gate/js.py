"""JS/TS target support: Babel-engine driver, file discovery, mutant generation."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config
from .model import Mutant, VerifyResult

ENGINE = Path(__file__).parent / "js" / "engine.mjs"

_JS_EXTS = {".js", ".jsx", ".ts", ".tsx"}
_JS_NOISE = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "out",
    "coverage",
    ".venv",
    "venv",
    ".mutation-gate",
}


def node_available() -> bool:
    return shutil.which("node") is not None


def bun_available() -> bool:
    return shutil.which("bun") is not None


def js_runtime_binary(js_runtime: str = "node") -> str:
    """Resolve the binary that spawns the Babel engine (and runs JS tests).

    Honors cfg.js_runtime ("node"|"bun"); falls back to node with a warning
    when "bun" is requested but bun is not on PATH.
    """
    if (js_runtime or "node").lower() == "bun":
        bun = shutil.which("bun")
        if bun:
            return bun
        print('⚠️  js_runtime = "bun" but bun is not on PATH — falling back to node.', file=sys.stderr)
    return shutil.which("node") or "node"


def js_runner_prefix(js_runtime: str = "node") -> list[str]:
    """Command prefix that runs specific JS test files under the runtime.

    node → `[node, --test]`; bun → `[bun, test]` (bun has no --test flag).
    Used by verify_js_project and --test-subset. Coverage collection always
    uses Node's LCOV reporter regardless of this setting.
    """
    binary = js_runtime_binary(js_runtime)
    return [binary, "test"] if Path(binary).name.lower().startswith("bun") else [binary, "--test"]


def babel_installed(project_root: Path) -> bool:
    return (project_root / "node_modules" / "@babel" / "parser").exists()


def js_available(project_root: Path) -> bool:
    """True if Node + the Babel packages the engine needs are installed."""
    return node_available() and babel_installed(project_root)


def detect_js(project_root: Path, language: str) -> bool:
    """Auto-detect a JS/TS project (explicit language setting wins)."""
    if language == "python":
        return False
    if language == "js":
        return True
    has_pkg = (project_root / "package.json").exists()
    has_src = any(_is_js_source(p) for p in _iter_js_files(project_root))
    return bool(has_pkg and has_src)


def _is_js_source(path: Path) -> bool:
    return path.suffix in _JS_EXTS


def _is_noise(path: Path) -> bool:
    return any(part in _JS_NOISE for part in path.parts)


_TEST_PART = {"test", "tests", "__tests__"}


def _is_test_file(path: Path) -> bool:
    rel = path
    parts = rel.parts
    if any(part in _TEST_PART for part in parts[:-1]):
        return True
    stem = path.stem
    if stem.endswith(".test") or stem.endswith(".spec"):
        return True
    return False


def _iter_js_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and _is_js_source(p) and not _is_noise(p) and not _is_test_file(p):
            yield p


def collect_js_files(root: Path, files: list[str] | None = None) -> list[Path]:
    """JS/TS source files (relative to root), optionally filtered by --files."""
    if files:
        wanted = {Path(f) for f in files}
        return [p for p in sorted(_iter_js_files(root)) if p.relative_to(root) in wanted]
    return sorted(_iter_js_files(root))


def collect_js_test_files(root: Path) -> list[Path]:
    """JS/TS test files (relative to root)."""
    return sorted(
        p for p in root.rglob("*") if p.is_file() and _is_js_source(p) and not _is_noise(p) and _is_test_file(p)
    )


# ---------------------------------------------------------------------------
# Coverage via Node's built-in test runner (LCOV reporter)
# ---------------------------------------------------------------------------


def _lcov_lines(lcov_text: str, project_root: Path) -> dict[Path, set[int]]:
    """Parse `node --test --test-reporter=lcov` output into file → executed lines.

    Paths are relative to project_root; files outside the project are dropped.
    """
    project_root = project_root.resolve()
    result: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in lcov_text.splitlines():
        if line.startswith("SF:"):
            raw = line[3:].replace("\\", "/")
            p = Path(raw)
            if not p.is_absolute():
                p = project_root / p
            try:
                current = p.resolve().relative_to(project_root)
            except ValueError:
                current = None
        elif line.startswith("DA:") and current is not None:
            try:
                lineno_s, count_s = line[3:].split(",", 1)
                if int(count_s) > 0:
                    result.setdefault(current, set()).add(int(lineno_s))
            except ValueError:
                continue
    return result


def js_covered_lines_for_test(
    project_root: Path,
    test_file: Path,
    timeout: int = 300,
) -> dict[Path, set[int]]:
    """Run `node --test --experimental-test-coverage` for one test file.

    Returns file → executed line numbers, paths relative to project_root.
    Empty dict on any failure.
    """
    if not node_available():
        return {}
    if not test_file.is_absolute():
        test_file = project_root / test_file
    cmd = [
        "node",
        "--test",
        "--experimental-test-coverage",
        "--test-reporter=lcov",
        str(test_file.relative_to(project_root)),
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    return _lcov_lines(proc.stdout, project_root)


def js_covered_lines_for_suite(project_root: Path, timeout: int = 300) -> dict[Path, set[int]]:
    """Run all test files under coverage; return file → executed lines."""
    if not node_available():
        return {}
    test_files = collect_js_test_files(project_root)
    if not test_files:
        return {}
    rels = [t.relative_to(project_root).as_posix() for t in test_files]
    cmd = ["node", "--test", "--experimental-test-coverage", "--test-reporter=lcov", *rels]
    try:
        proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    return _lcov_lines(proc.stdout, project_root)


def _js_coverage_task(root_str: str, test_path_str: str, timeout: int) -> tuple[str, dict]:
    cov = js_covered_lines_for_test(Path(root_str), Path(test_path_str), timeout=timeout)
    return test_path_str, cov


def collect_js_per_file_coverage(
    project_root: Path,
    test_files: list[Path],
    timeout: int = 300,
    workers: int = 4,
) -> dict[Path, set[Path]]:
    """Map each source file → set of test files that execute lines in it."""
    if not node_available() or not test_files:
        return {}
    from concurrent.futures import ProcessPoolExecutor

    project_root = project_root.resolve()

    results: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_js_coverage_task, str(project_root), str(tf), timeout) for tf in test_files]
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


def generate_js_mutants(
    project_root: Path,
    file_rel: Path,
    operators: list[str] | None = None,
    binary: str | None = None,
) -> list[Mutant]:
    """Run the Babel engine over one file and return Mutant objects.

    `binary` overrides the runtime executable (see js_runtime_binary); callers
    processing many files resolve it once and pass it through.
    """
    # Lazy import: runner.py imports js.js_runner_prefix inside subset_prefix.
    from .runner import _resolve_cmd

    node = binary or shutil.which("node")
    file_abs = (project_root / file_rel).resolve()
    proc = subprocess.run(
        _resolve_cmd([node, str(ENGINE), str(project_root), str(file_abs)]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(project_root),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"JS engine failed for {file_rel}: {proc.stderr[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"JS engine returned invalid JSON for {file_rel}")

    original = (project_root / file_rel).read_text(encoding="utf-8")
    mutants: list[Mutant] = []
    for m in data:
        op = m["operator"]
        if operators and op not in operators:
            continue
        mutants.append(
            Mutant(
                id=len(mutants),
                file=Path(file_rel.as_posix()),
                lineno=m["line"],
                operator=op,
                before=m["before"],
                after=m["after"],
                source=m["source"],
                original=original,
            )
        )
    return mutants


def generate_js_mutants_batch(
    project_root: Path,
    files_rel: list[Path],
    binary: str | None = None,
    operators: list[str] | None = None,
) -> dict[Path, list[Mutant]]:
    """Generate mutants for many files in one engine process (NDJSON mode).

    Amortizes runtime startup + Babel require across all files. Returns
    file → mutants using the relative paths exactly as passed (posix form).
    Raises RuntimeError on engine failure, mirroring generate_js_mutants.
    """
    binary = binary or shutil.which("node")
    rels = [f.as_posix() for f in files_rel]
    proc = subprocess.run(
        [binary, str(ENGINE), str(project_root), "--batch", *rels],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180 * max(1, len(rels)),
        cwd=str(project_root),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"JS engine batch failed: {proc.stderr[:500]}")

    out: dict[Path, list[Mutant]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        file_rel = Path(entry["file"])
        if "error" in entry:
            raise RuntimeError(f"JS engine failed for {file_rel}: {entry['error']}")
        original = (project_root / file_rel).read_text(encoding="utf-8")
        mutants: list[Mutant] = []
        for m in entry["mutants"]:
            op = m["operator"]
            if operators and op not in operators:
                continue
            mutants.append(
                Mutant(
                    id=len(mutants),
                    file=file_rel,
                    lineno=m["line"],
                    operator=op,
                    before=m["before"],
                    after=m["after"],
                    source=m["source"],
                    original=original,
                )
            )
        out[file_rel] = mutants
    return out


def verify_js_project(
    root: Path,
    test_file: Path,
    cfg: Config,
) -> VerifyResult:
    """JS analog of verify_project: per-test-file contribution, no coverage filter."""
    from .runner import Runner

    test_file = Path(test_file)
    if not test_file.is_absolute():
        test_file = root / test_file
    test_rel = test_file.relative_to(root).as_posix()

    covered = js_covered_lines_for_test(root, test_file, timeout=max(cfg.timeout, 300))
    cov_available = bool(covered)

    engine_bin = js_runtime_binary(cfg.js_runtime)
    rels: list[Path] = []
    for f in collect_js_files(root):
        rel = f.relative_to(root)
        if cov_available and rel not in covered:
            continue
        rels.append(rel)
    try:
        batch = generate_js_mutants_batch(root, rels, binary=engine_bin, operators=cfg.operators)
    except RuntimeError:
        batch = None

    mutants: list[Mutant] = []
    ops = cfg.operators
    for rel in rels:
        items = batch.get(rel) if batch is not None else None
        if items is None:
            try:
                items = generate_js_mutants(root, rel, operators=ops, binary=engine_bin)
            except RuntimeError:
                continue
        for m in items:
            if cov_available and m.lineno not in covered.get(m.file, set()):
                continue
            mutants.append(m)

    cache_file = root / cfg.cache_file if cfg.cache else None
    cfg.test_command = shlex.join([*js_runner_prefix(cfg.js_runtime), test_rel])
    runner = Runner(
        root,
        test_command=cfg.test_command,
        timeout=cfg.timeout,
        workers=cfg.workers,
        cache_file=cache_file,
    )
    results, _cached = runner.run(mutants)

    killed, survived = 0, 0
    survivors: list[Mutant] = []
    for r in results:
        if r.status == "killed":
            killed += 1
        elif r.status == "survived":
            survived += 1
            survivors.append(r.mutant)

    return VerifyResult(
        test_file=test_file,
        reachable=len(mutants),
        killed=killed,
        survived=survived,
        invalid=0,
        survivors=survivors,
        coverage_available=cov_available,
    )
