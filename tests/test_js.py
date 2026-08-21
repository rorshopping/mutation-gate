"""JS/TS target tests. Skip if Node or the demo's Babel deps are unavailable."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from mutation_gate import js as js_mod
from mutation_gate.config import Config, load_config

REPO = Path(__file__).resolve().parents[1]
DEMO_JS = REPO / "examples" / "demo-js"

_HAS_NODE = shutil.which("node") is not None
_HAS_DEPS = DEMO_JS.exists() and (DEMO_JS / "node_modules" / "@babel" / "parser").exists()

needs_js = pytest.mark.skipif(
    not (_HAS_NODE and _HAS_DEPS),
    reason="needs Node and the demo-js Babel deps installed",
)


def _tmp_project(tmp_path) -> Path:
    (tmp_path / "package.json").write_text('{"name":"p","scripts":{"test":"node --test"}}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.js").write_text(
        "export function clamp(v, lo, hi) {\n"
        "  if (v < lo) return lo;\n"
        "  if (v > hi) return hi;\n"
        "  return v;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "core.test.js").write_text("import { test } from 'node:test';\ntest('x', () => {});\n", encoding="utf-8")
    return tmp_path


def test_detect_js_on_js_project(tmp_path):
    p = _tmp_project(tmp_path)
    assert js_mod.detect_js(p, "auto") is True


def test_detect_js_false_on_python(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert js_mod.detect_js(tmp_path, "auto") is False


def test_detect_js_language_setting(tmp_path):
    p = _tmp_project(tmp_path)
    assert js_mod.detect_js(p, "python") is False
    assert js_mod.detect_js(p, "js") is True


def test_collect_js_files_excludes_tests(tmp_path):
    p = _tmp_project(tmp_path)
    files = js_mod.collect_js_files(p)
    names = {f.name for f in files}
    assert "core.js" in names
    assert "core.test.js" not in names


def test_collect_js_files_files_filter(tmp_path):
    p = _tmp_project(tmp_path)
    files = js_mod.collect_js_files(p, files=["src/core.js"])
    assert [f.name for f in files] == ["core.js"]


def test_collect_js_test_files():
    tests = js_mod.collect_js_test_files(DEMO_JS)
    names = {t.name for t in tests}
    assert "math.real.test.js" in names
    assert "math.theater.test.js" in names
    assert "math.js" not in names


def test_lcov_parse_backslash_paths(tmp_path):
    lcov = "SF:src\\math.js\nDA:1,1\nDA:2,0\nDA:3,5\nLF:3\nLH:2\n"
    out = js_mod._lcov_lines(lcov, tmp_path)
    assert out == {Path("src/math.js"): {1, 3}}


def test_lcov_parse_absolute_outside_project(tmp_path):
    lcov = f"SF:{tmp_path.parent}\\other.js\nDA:1,1\n"
    out = js_mod._lcov_lines(lcov, tmp_path)
    assert out == {}


@needs_js
def test_js_covered_lines_for_test():
    cov = js_mod.js_covered_lines_for_test(DEMO_JS, Path("test/math.real.test.js"))
    assert cov
    assert Path("src/math.js") in cov
    assert cov[Path("src/math.js")]


@needs_js
def test_js_covered_lines_for_suite():
    cov = js_mod.js_covered_lines_for_suite(DEMO_JS)
    assert cov.get(Path("src/math.js"))


@needs_js
def test_collect_js_per_file_coverage():
    test_files = js_mod.collect_js_test_files(DEMO_JS)
    mapping = js_mod.collect_js_per_file_coverage(DEMO_JS, test_files, workers=2)
    assert Path("src/math.js") in mapping
    names = {t.name for t in mapping[Path("src/math.js")]}
    assert {"math.real.test.js", "math.theater.test.js"} <= names


@needs_js
def test_verify_js_uses_coverage():
    cfg = load_config(DEMO_JS)
    cfg.operators = ["comparison"]
    cfg.workers = 2
    vr = js_mod.verify_js_project(DEMO_JS, Path("test/math.real.test.js"), cfg)
    assert vr.coverage_available is True
    assert vr.reachable > 0


@needs_js
def test_js_run_test_subset_cli(tmp_path):
    import re
    import subprocess

    def score(args):
        proc = subprocess.run(
            [sys.executable, "-m", "mutation_gate.cli", "run", ".", "--no-cache", *args],
            cwd=str(DEMO_JS),
            capture_output=True,
            text=True,
            timeout=600,
        )
        m = re.search(r"Mutation score:\s+([\d.]+)%", proc.stdout)
        assert m, proc.stdout + proc.stderr
        return float(m.group(1))

    full = score([])
    subset = score(["--test-subset"])
    assert subset == full


@needs_js
def test_generate_js_mutants_produces_comparison():
    rel = Path("src/math.js")
    mutants = js_mod.generate_js_mutants(DEMO_JS, rel)
    assert len(mutants) > 10
    ops = {m.operator for m in mutants}
    assert "comparison" in ops
    assert "return_none" in ops
    assert "aug_assign" in ops  # countAbove uses `count += 1`
    for m in mutants:
        assert m.file == rel
        assert m.source != m.original
    names = [m.operator for m in mutants if m.lineno == 2]
    assert any(m.before == "value < lo" for m in mutants)


@needs_js
def test_generate_js_aug_assign_flips():
    mutants = js_mod.generate_js_mutants(DEMO_JS, Path("src/math.js"), operators=["aug_assign"])
    assert mutants
    assert any("+=" in m.before and "-=" in m.after for m in mutants)


@needs_js
def test_generate_js_mutants_operator_filter():
    mutants = js_mod.generate_js_mutants(DEMO_JS, Path("src/math.js"), operators=["comparison"])
    assert mutants
    assert {m.operator for m in mutants} == {"comparison"}


@needs_js
def test_verify_js_real_beats_theater():
    cfg = load_config(DEMO_JS)
    cfg.operators = ["comparison"]
    cfg.workers = 2

    theater = js_mod.verify_js_project(DEMO_JS, Path("test/math.theater.test.js"), cfg)
    real = js_mod.verify_js_project(DEMO_JS, Path("test/math.real.test.js"), cfg)

    assert theater.contribution is not None
    assert real.contribution is not None
    assert real.contribution > theater.contribution + 0.2
    assert theater.survivors
    assert len(real.survivors) < len(theater.survivors)


# ---------------------------------------------------------------------------
# js_runtime ("bun" | "node") support
# ---------------------------------------------------------------------------


def _which_fake(bun=None, node=None):
    def fake(name, *a, **kw):
        if name == "bun":
            return bun
        if name == "node":
            return node
        return None

    return fake


def test_js_runner_prefix_defaults_to_node(monkeypatch):
    monkeypatch.setattr(js_mod.shutil, "which", _which_fake(node="/usr/bin/node"))
    assert js_mod.js_runner_prefix("node") == ["/usr/bin/node", "--test"]


def test_js_runner_prefix_bun(monkeypatch):
    monkeypatch.setattr(js_mod.shutil, "which", _which_fake(bun="/usr/bin/bun"))
    assert js_mod.js_runner_prefix("bun") == ["/usr/bin/bun", "test"]


def test_js_runtime_binary_bun_missing_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(js_mod.shutil, "which", _which_fake(node="/usr/bin/node"))
    assert js_mod.js_runtime_binary("bun") == "/usr/bin/node"
    err = capsys.readouterr().err
    assert "falling back to node" in err


def test_generate_js_mutants_uses_given_binary(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class P:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return P()

    monkeypatch.setattr(js_mod.subprocess, "run", fake_run)
    rel = Path("src/core.js")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.js").write_text("let x = 1;\n", encoding="utf-8")
    js_mod.generate_js_mutants(tmp_path, rel, binary="/opt/bin/bun")
    assert captured["cmd"][0] == "/opt/bin/bun"
    assert captured["cmd"][-1].endswith("core.js")


def test_subset_prefix_bun_vs_node(monkeypatch):
    from mutation_gate.runner import subset_prefix

    monkeypatch.setattr(js_mod.shutil, "which", _which_fake(bun="/usr/bin/bun"))
    assert subset_prefix(["npm", "test"], js_runtime="bun") == ["/usr/bin/bun", "test"]
    monkeypatch.setattr(js_mod.shutil, "which", _which_fake(node="/usr/bin/node"))
    assert subset_prefix(["npm", "test"], js_runtime="node") == ["/usr/bin/node", "--test"]


def test_subset_prefix_pytest_unaffected_by_js_runtime():
    import sys

    from mutation_gate.runner import subset_prefix

    # resolve_test_cmd rewrites `pytest ...` to `[python, -m, pytest, ...]`
    cmd = subset_prefix([sys.executable, "-m", "pytest", "-q"], js_runtime="bun")
    assert cmd == [sys.executable, "-m", "pytest", "-q"]


def test_config_loads_js_runtime(tmp_path):
    (tmp_path / ".mutation-gate.toml").write_text('js_runtime = "bun"\n', encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.js_runtime == "bun"


def test_config_default_js_runtime_is_node():
    cfg = Config()
    assert cfg.js_runtime == "node"
