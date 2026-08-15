"""Unit tests for config loading and file collection."""

import textwrap
from pathlib import Path

from mutation_gate.config import collect_python_files, load_config


def test_defaults_when_no_config(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.test_command == "pytest"
    assert cfg.timeout == 60
    assert "**/tests/**" in cfg.exclude_globs


def test_pyproject_config_loaded(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.mutation-gate]
            test_command = "pytest -q"
            timeout = 30
            """
        )
    )
    cfg = load_config(tmp_path)
    assert cfg.test_command == "pytest -q"
    assert cfg.timeout == 30
    assert cfg.source == "pyproject.toml [tool.mutation-gate]"


def test_dot_file_overrides_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mutation-gate]\ntimeout = 30\n")
    (tmp_path / ".mutation-gate.toml").write_text("timeout = 15\nworkers = 8\n")
    cfg = load_config(tmp_path)
    assert cfg.timeout == 15
    assert cfg.workers == 8


def test_bom_prefixed_config_file_loads(tmp_path):
    path = tmp_path / "pp2.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b'timeout = 42\nworkers = 2\n')
    cfg = load_config(tmp_path, str(path))
    assert cfg.timeout == 42
    assert cfg.workers == 2


def test_utf16_config_file_loads(tmp_path):
    path = tmp_path / "utf16.toml"
    path.write_bytes(b"\xff\xfe" + "timeout = 7\n".encode("utf-16-le"))
    cfg = load_config(tmp_path, str(path))
    assert cfg.timeout == 7


def test_collect_files(tmp_path):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "test_a.py").write_text("def test():\n    pass\n")
    cfg = load_config(tmp_path)
    files = collect_python_files(tmp_path, cfg)
    assert any(f.name == "a.py" for f in files)
    assert not any(f.name.endswith("test_a.py") for f in files)


def test_collect_excludes_files_under_dot_dirs(tmp_path):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / ".venv" / "Lib" / "site-packages").mkdir(parents=True)
    (tmp_path / ".venv" / "Lib" / "site-packages" / "dep.py").write_text("y = 1\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.py").write_text("z = 1\n")
    cfg = load_config(tmp_path)
    files = collect_python_files(tmp_path, cfg)
    names = {f.name for f in files}
    assert names == {"a.py"}
