"""Tests for the git diff delta-mode parser."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mutation_gate.diff import git_changed_lines, git_changed_test_files


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


def test_js_files_now_tracked(repo: Path):
    (repo / "src").mkdir()
    (repo / "src" / "math.js").write_text("export const a = 1;\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "src" / "math.js").write_text("export const a = 2;\n", encoding="utf-8")
    changed = git_changed_lines(repo)
    assert Path("src/math.js") in changed


def test_changed_test_files_includes_untracked(repo: Path):
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "test_theater.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    files = git_changed_test_files(repo)
    assert files is not None
    assert "a.py" in files
    assert "test_theater.py" in files


def test_changed_test_files_nested_subdir(repo: Path):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "src" / "test_a.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    files = git_changed_test_files(repo / "src")
    assert files is not None
    assert "test_a.py" in files
    assert "src/test_a.py" not in files
