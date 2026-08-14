"""Setup friction: the tool should run tests with the project's own venv interpreter."""

import pytest

from mutation_gate.runner import Runner, detect_project_python, resolve_test_cmd, subset_prefix


def test_detect_project_python_returns_none_without_venv(tmp_path):
    assert detect_project_python(tmp_path) is None


@pytest.mark.parametrize("rel", ["Scripts/python.exe", "bin/python"])
def test_detect_project_python_finds_venv(tmp_path, rel):
    p = tmp_path / ".venv" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    assert detect_project_python(tmp_path) == str(p)


def test_detect_project_python_prefers_dot_venv_over_venv(tmp_path):
    a = tmp_path / ".venv" / "bin" / "python"
    b = tmp_path / "venv" / "bin" / "python"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    assert detect_project_python(tmp_path) == str(a)


def test_resolve_test_cmd_pytest_uses_project_python(tmp_path):
    venv = str(tmp_path / ".venv" / "bin" / "python")
    cmd = resolve_test_cmd("pytest", venv)
    assert cmd[0] == venv
    assert cmd[1:3] == ["-m", "pytest"]


def test_resolve_test_cmd_python_substituted(tmp_path):
    venv = str(tmp_path / ".venv" / "bin" / "python")
    cmd = resolve_test_cmd("python -m pytest -q", venv)
    assert cmd[0] == venv
    assert cmd[1:4] == ["-m", "pytest", "-q"]


def test_resolve_test_cmd_falls_back_to_own_interpreter():
    cmd = resolve_test_cmd("pytest")
    assert cmd[0] != "pytest"
    assert cmd[1:3] == ["-m", "pytest"]


def test_subset_prefix_keeps_venv_interpreter():
    py = "/proj/.venv/bin/python"
    assert subset_prefix([py, "-m", "pytest"], py) == [py, "-m", "pytest", "-q"]


def test_runner_auto_detects_project_venv(tmp_path):
    for rel in ("Scripts/python.exe", "bin/python"):
        p = tmp_path / ".venv" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    r = Runner(tmp_path, test_command="pytest")
    assert r.project_python is not None
    assert r.project_python.startswith(str(tmp_path))
    assert r._test_cmd()[0] == r.project_python
