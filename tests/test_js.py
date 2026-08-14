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


@needs_js
def test_generate_js_mutants_produces_comparison():
    rel = Path("src/math.js")
    mutants = js_mod.generate_js_mutants(DEMO_JS, rel)
    assert len(mutants) > 10
    ops = {m.operator for m in mutants}
    assert "comparison" in ops
    assert "return_none" in ops
    for m in mutants:
        assert m.file == rel
        assert m.source != m.original
    names = [m.operator for m in mutants if m.lineno == 2]
    assert any(m.before == "value < lo" for m in mutants)


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
