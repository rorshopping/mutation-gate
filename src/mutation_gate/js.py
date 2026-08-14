"""JS/TS target support: Babel-engine driver, file discovery, mutant generation."""

from __future__ import annotations

import json
import shutil
import subprocess
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


def generate_js_mutants(
    project_root: Path,
    file_rel: Path,
    operators: list[str] | None = None,
) -> list[Mutant]:
    """Run the Babel engine over one file and return Mutant objects."""
    node = shutil.which("node")
    file_abs = (project_root / file_rel).resolve()
    proc = subprocess.run(
        [node, str(ENGINE), str(project_root), str(file_abs)],
        capture_output=True,
        text=True,
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

    mutants: list[Mutant] = []
    ops = cfg.operators
    for f in collect_js_files(root):
        rel = f.relative_to(root)
        try:
            mutants.extend(generate_js_mutants(root, rel, operators=ops))
        except RuntimeError:
            continue

    cache_file = root / cfg.cache_file if cfg.cache else None
    cfg.test_command = f"node --test {test_rel}"
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
        coverage_available=False,
    )
