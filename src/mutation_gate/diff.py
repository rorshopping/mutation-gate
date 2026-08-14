"""Delta mode: only mutate lines changed in the working tree vs HEAD (git)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_SOURCE_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _repo_top(root: Path) -> Path | None:
    """Absolute path of the git repo top level containing `root`, else None."""
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _rebase(root: Path, top: Path, name: str) -> Path | None:
    """Rebase a git-reported path onto `root`; None if it lies outside.

    `git diff` reports repo-root-relative paths while `git ls-files` reports
    cwd-relative ones, so try both bases and prefer whichever exists.
    """
    root = root.resolve()
    top = top.resolve()

    def rel(base: Path) -> Path | None:
        try:
            return (base / name).resolve().relative_to(root)
        except ValueError:
            return None

    for base in (root, top):
        if (base / name).exists():
            return rel(base)
    return rel(root) or rel(top)


def git_changed_lines(root: Path) -> dict[Path, set[int]] | None:
    """Return {relative_path: set_of_new_line_numbers} for changes vs HEAD.

    Returns None if git is unavailable or the path isn't a repo.
    """
    root = root.resolve()
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0:
        return None
    top = _repo_top(root)
    if top is None:
        return None

    name_proc = _git(root, "diff", "HEAD", "--name-only", "-z")
    if name_proc.returncode != 0:
        return None
    names = [n for n in name_proc.stdout.split("\0") if n]

    result: dict[Path, set[int]] = {}
    for name in names:
        rel = _rebase(root, top, name)
        if rel is None or not rel.name.endswith(_SOURCE_EXTS):
            continue
        diff = _git(root, "diff", "HEAD", "--unified=0", "--", name)
        if diff.returncode != 0:
            continue
        lines: set[int] = set()
        cur = 0
        for raw in diff.stdout.splitlines():
            m = _HUNK_RE.match(raw)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or 1)
                cur = start
                continue
            if raw.startswith("+") and not raw.startswith("+++"):
                lines.add(cur)
                cur += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                pass  # removed lines don't exist in new file
            elif raw.startswith(" "):
                cur += 1
        if lines:
            result[rel] = lines
    return result


def git_changed_test_files(root: Path) -> list[str] | None:
    """Paths (relative to root, posix) that are new or modified vs HEAD — including untracked.

    Returns None if git is unavailable or the path isn't a repo.
    """
    root = root.resolve()
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0:
        return None
    top = _repo_top(root)
    if top is None:
        return None

    tracked = _git(root, "diff", "HEAD", "--name-only", "-z")
    if tracked.returncode != 0:
        return None
    files = [n for n in tracked.stdout.split("\0") if n]

    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode == 0:
        files += [n for n in untracked.stdout.split("\0") if n]

    result: list[str] = []
    for name in files:
        rel = _rebase(root, top, name)
        if rel is not None:
            result.append(rel.as_posix())
    return result
