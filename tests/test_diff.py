"""Tests for the git diff delta-mode parser."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mutation_gate.diff import git_changed_lines


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_not_a_git_repo(tmp_path: Path):
    assert git_changed_lines(tmp_path) is None


def test_no_changes_returns_empty(repo: Path):
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    assert git_changed_lines(repo) == {}


def test_added_line_is_tracked(repo: Path):
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 1\nif x:\n    print(x)\n", encoding="utf-8")
    changed = git_changed_lines(repo)
    assert Path("a.py") in changed
    assert 2 in changed[Path("a.py")]
    assert 3 in changed[Path("a.py")]


def test_non_python_files_ignored(repo: Path):
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "README.md").write_text("bye\n", encoding="utf-8")
    changed = git_changed_lines(repo)
    assert Path("a.py") in changed
    assert Path("README.md") not in changed
