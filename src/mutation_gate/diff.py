"""Delta mode: only mutate lines changed in the working tree vs HEAD (git)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def git_changed_lines(root: Path) -> dict[Path, set[int]] | None:
    """Return {relative_path: set_of_new_line_numbers} for changes vs HEAD.

    Returns None if git is unavailable or the path isn't a repo.
    """
    root = root.resolve()
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0:
        return None

    name_proc = _git(root, "diff", "HEAD", "--name-only", "-z")
    if name_proc.returncode != 0:
        return None
    names = [n for n in name_proc.stdout.split("\0") if n]

    result: dict[Path, set[int]] = {}
    for name in names:
        if not name.endswith(".py"):
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
            result[Path(name)] = lines
    return result
