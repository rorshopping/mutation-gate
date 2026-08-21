"""Configuration loading: defaults < [tool.mutation-gate] in pyproject.toml < .mutation-gate.toml < CLI."""

from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    test_command: str = "pytest"
    timeout: int = 60
    workers: int = 4
    include_globs: list[str] = field(default_factory=lambda: ["**/*.py"])
    exclude_globs: list[str] = field(
        default_factory=lambda: [
            "**/test_*.py",
            "**/*_test.py",
            "**/tests/**",
            "**/conftest.py",
            "**/.git/**",
            "**/.venv/**",
            "**/venv/**",
            "**/__pycache__/**",
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
        ]
    )
    source: str = "defaults"
    project_root: Path | None = None
    cache: bool = True
    cache_file: str = ".mutation-gate/cache.json"
    coverage_guided: bool = False
    test_subset: bool = False
    mutate_docstrings: bool = False
    operators: list[str] | None = None
    language: str = "auto"  # "auto" | "python" | "js" | "java" | "csharp" | "cpp"
    # Runtime used to spawn the JS Babel engine and run per-mutant tests.
    # "bun" opts into Bun (faster process startup); coverage collection stays on Node.
    js_runtime: str = "node"  # "node" | "bun"

    def resolve(self, root: Path) -> "Config":
        self.project_root = root.resolve()
        return self

    def effective_operators(self) -> dict[str, callable]:
        from .operators import OPERATORS

        if not self.operators:
            return dict(OPERATORS)
        chosen = {}
        for name in self.operators:
            if name in OPERATORS:
                chosen[name] = OPERATORS[name]
        return chosen


def _apply(cfg: Config, data: dict, source: str) -> Config:
    if not data:
        return cfg
    for key, value in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    cfg.source = source
    return cfg


def _read_toml(path: Path) -> dict:
    """Read a TOML file, tolerating UTF-8/UTF-16 BOMs (PowerShell writes them)."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8")
    elif data.startswith(b"\xff\xfe"):
        text = data[2:].decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        text = data[2:].decode("utf-16-be")
    else:
        text = data.decode("utf-8")
    return tomllib.loads(text)


def load_config(root: Path, explicit_file: str | None = None) -> Config:
    cfg = Config()
    root = root.resolve()

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = _read_toml(pyproject)
            cfg = _apply(cfg, data.get("tool", {}).get("mutation-gate", {}), "pyproject.toml [tool.mutation-gate]")
        except tomllib.TOMLDecodeError:
            pass

    if explicit_file:
        path = Path(explicit_file)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            cfg = _apply(cfg, _read_toml(path), str(path))
    else:
        auto = root / ".mutation-gate.toml"
        if auto.exists():
            cfg = _apply(cfg, _read_toml(auto), ".mutation-gate.toml")

    return cfg.resolve(root)


def collect_python_files(root: Path, cfg: Config) -> list[Path]:
    """Files to mutate: match include globs, don't match exclude globs.

    Uses pathlib.glob so `**` matches zero-or-more directories natively.
    """
    root = root.resolve()
    excluded: set[Path] = set()
    for pat in cfg.exclude_globs:
        for p in root.glob(pat):
            if p.is_file():
                excluded.add(p.resolve())
            elif p.is_dir():
                excluded.update(q.resolve() for q in p.rglob("*") if q.is_file())
    included: set[Path] = set()
    for pat in cfg.include_globs:
        included |= {p.resolve() for p in root.glob(pat) if p.is_file()}
    files: list[Path] = []
    for p in sorted(included):
        if p in excluded or p.suffix != ".py":
            continue
        files.append(p)
    return files


def collect_test_files(root: Path, cfg: Config) -> list[Path]:
    """Test files matching test globs (excluding noise dirs only).

    Used by --test-subset to build per-source-file test attribution. The
    user's mutation exclude_globs are NOT applied here — they default to
    excluding tests, which would defeat the purpose.
    """
    root = root.resolve()
    noise = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
    }
    patterns = ["**/test_*.py", "**/*_test.py", "**/tests/**/*.py"]
    found: set[Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if any(part in noise for part in rel.parts):
                continue
            found.add(p.resolve())
    return sorted(found)
