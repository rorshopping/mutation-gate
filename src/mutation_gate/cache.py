"""Disk cache for mutant run results.

A run is keyed by a *fingerprint* of everything that determines an outcome
(test command, timeout, and the content of every Python file in the project).
If the fingerprint matches a previous run, killed/survived results are
replayed instead of re-running the suite — a large speed-up for repeated runs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_CACHE_VERSION = 1

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


def fingerprint(project_root: Path, test_command: str, timeout: int) -> str:
    """Hash of everything that can change a mutant's outcome.

    Covers the test command plus every file that would be copied into a worktree
    (i.e. all files not under an ignored directory), so fixture changes that
    affect test behavior also invalidate the cache.
    """
    h = hashlib.sha1()
    h.update(f"{test_command}\0{timeout}\0".encode())
    root = project_root.resolve()
    files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part in _IGNORE_DIRS for part in rel.parts):
            continue
        files.append(p)
    for p in sorted(files, key=lambda x: str(x).lower()):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        # Stream large files; skip pathological blobs (>50 MiB) to stay fast.
        if p.stat().st_size <= 50 * 1024 * 1024:
            try:
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
            except OSError:
                pass
        h.update(b"\0")
    return h.hexdigest()


def mutant_key(mutant_source: str, rel_file: str, operator: str) -> str:
    h = hashlib.sha1()
    h.update(f"{rel_file}\0{operator}\0".encode())
    h.update(mutant_source.encode())
    return h.hexdigest()


def load_cache(cache_file: Path) -> dict | None:
    """Return {mutant_key: result_dict} or None if unusable."""
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(cache_file: Path, fingerprint_val: str, results: dict[str, dict]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _CACHE_VERSION,
        "fingerprint": fingerprint_val,
        "results": results,
    }
    tmp = cache_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(cache_file)


def load_results(cache_file: Path, fp: str) -> dict[str, dict]:
    """Load cache entries valid for this fingerprint."""
    data = load_cache(cache_file)
    if not data or data.get("fingerprint") != fp:
        return {}
    return data.get("results", {})
