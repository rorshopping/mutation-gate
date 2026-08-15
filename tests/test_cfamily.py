"""Java / C# / C++ target tests (pure-Python tokenizer engine)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mutation_gate import cfamily as cf
from mutation_gate.config import Config, load_config

REPO = Path(__file__).resolve().parents[1]
DEMO_CSHARP = REPO / "examples" / "demo-csharp"
DEMO_CPP = REPO / "examples" / "demo-cpp"
DEMO_JAVA = REPO / "examples" / "demo-java"

_HAS_DOTNET = shutil.which("dotnet") is not None
_HAS_CMake = shutil.which("cmake") is not None

needs_dotnet = pytest.mark.skipif(not _HAS_DOTNET, reason="needs dotnet SDK")

JAVA_SRC = """package calc;
public class Math {
    public static int clamp(int v, int lo, int hi) {
        if (v < lo) return lo;
        if (v > hi) return hi;
        return v;
    }
    public static boolean isZero(int x) {
        return !(x != 0);
    }
    public static long hex() {
        long h = 0x1FL;
        String s = "hello";
        return h;
    }
}
"""

CSHARP_SRC = """using System;
namespace Calc {
    public static class Math {
        public static int Clamp(int v, int lo, int hi) {
            if (v < lo) return lo;
            if (v > hi) return hi;
            return v;
        }
        public static string Label(int n) {
            string a = $"n={n}";
            string b = @"verbatim";
            if (n > 0) return "pos";
            return "neg";
        }
        public static int Add(int a, int b) {
            return a + b;
        }
    }
}
"""

CPP_SRC = """#include <string>
namespace calc {
int clamp(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}
bool has(const std::string& s) {
    auto r = R"delim(raw " string)delim";
    auto p = u8"prefixed";
    int h = 0x1F;
    return !s.empty() && s.length() > 0;
}
int double_ptr(const double* values, int count) {
    return values[count];
}
}
"""


# ---------------------------------------------------------------------------
# Tokenizer / generation unit tests
# ---------------------------------------------------------------------------


def _ops(source, lang, operators=None):
    return cf.generate_cfamily_mutants(source, Path("Math.cs" if lang == "csharp" else "math.cpp" if lang == "cpp" else "Math.java"), lang, operators=operators)


def test_java_operators_all():
    muts = _ops(JAVA_SRC, "java")
    ops = {m.operator for m in muts}
    assert {"comparison", "negate_condition", "remove_not", "num_literal", "str_literal", "remove_stmt"} <= ops
    assert "return_none" not in ops
    for m in muts:
        assert m.file == Path("Math.java")
        assert m.source != m.original
        assert m.after == "" or m.after != m.before


def test_java_negate_condition_after():
    muts = _ops(JAVA_SRC, "java", operators=["negate_condition"])
    clamps = [m for m in muts if m.lineno == 4]
    assert clamps and clamps[0].after == "!(v < lo)"


def test_java_num_literal_hex_suffix():
    muts = _ops(JAVA_SRC, "java", operators=["num_literal"])
    hexm = [m for m in muts if m.before == "0x1FL"]
    assert hexm and hexm[0].after == "0x20L"


def test_java_str_literal():
    muts = _ops(JAVA_SRC, "java", operators=["str_literal"])
    assert any(m.before == '"hello"' and m.after == '""' for m in muts)


def test_csharp_operators():
    muts = _ops(CSHARP_SRC, "csharp")
    ops = {m.operator for m in muts}
    assert {"comparison", "negate_condition", "str_literal", "binop", "remove_stmt"} <= ops
    assert "return_none" not in ops


def test_csharp_prefixed_strings_skipped():
    muts = _ops(CSHARP_SRC, "csharp")
    for m in muts:
        if m.operator == "str_literal":
            assert m.before not in ('"$n={n}"', '"@"verbatim""'), m.before
    assert any(m.before == '"pos"' and m.after == '""' for m in muts)


def test_csharp_boolop_and_negate():
    muts = _ops(CSHARP_SRC, "csharp")
    assert any(m.operator == "negate_condition" and m.after == "!(n > 0)" for m in muts)


def test_cpp_operators():
    muts = _ops(CPP_SRC, "cpp")
    ops = {m.operator for m in muts}
    assert {"comparison", "negate_condition", "num_literal", "remove_not", "boolop", "remove_stmt"} <= ops
    assert "str_literal" not in ops  # u8"prefixed" and raw string are prefixed → skipped


def test_cpp_raw_and_prefixed_strings_skipped():
    muts = _ops(CPP_SRC, "cpp")
    for m in muts:
        assert m.operator != "str_literal"


def test_cpp_num_literal_hex():
    muts = _ops(CPP_SRC, "cpp", operators=["num_literal"])
    assert any(m.before == "0x1F" and m.after == "0x20" for m in muts)


def test_cpp_pointer_star_not_binop():
    muts = _ops(CPP_SRC, "cpp", operators=["binop"])
    for m in muts:
        assert m.lineno != 17, f"pointer declaration mutated: {m.before}"


def test_cpp_declaration_not_removed():
    src = "int global;\nvoid f() {\n  int x = 1;\n  int y;\n  return;\n}\n"
    muts = cf.generate_cfamily_mutants(src, Path("a.cpp"), "cpp", operators=["remove_stmt"])
    removed = [m.before for m in muts]
    assert "int global;" not in removed  # pure declaration (no =) → blocked
    assert "int y;" not in removed  # declaration without initializer → blocked
    assert any(m.before == "int x = 1;" for m in muts)  # initialized → still removable
    assert any(m.before == "return;" for m in muts)


def test_generate_operator_filter():
    muts = _ops(JAVA_SRC, "java", operators=["comparison"])
    assert muts
    assert {m.operator for m in muts} == {"comparison"}


def test_generate_empty_source():
    assert cf.generate_cfamily_mutants("", Path("a.java"), "java") == []


def test_generate_dedup_and_ids():
    muts = _ops(JAVA_SRC, "java")
    ids = [m.id for m in muts]
    assert ids == list(range(len(muts)))
    sources = {m.source for m in muts}
    assert len(sources) == len(muts)


# ---------------------------------------------------------------------------
# File discovery / detection
# ---------------------------------------------------------------------------


def _proj(tmp_path, lang):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "core.java").write_text(JAVA_SRC, encoding="utf-8")
    (tests / "CoreTest.java").write_text("class CoreTest {}\n", encoding="utf-8")
    (tmp_path / "noise.java").write_text("class N {}\n", encoding="utf-8")
    (tests / "core.cs").write_text(CSHARP_SRC, encoding="utf-8")
    (tests / "core_test.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    return tmp_path


def test_collect_files_filters_tests_and_noise(tmp_path):
    p = _proj(tmp_path, "java")
    files = cf.collect_files(p, "java")
    rels = [f.relative_to(p).as_posix() for f in files]
    assert "src/core.java" in rels
    assert "noise.java" in rels
    assert "tests/CoreTest.java" not in rels
    assert "tests/core.cs" not in rels
    assert "tests/core_test.cpp" not in rels


def test_collect_files_filter_arg(tmp_path):
    p = _proj(tmp_path, "java")
    files = cf.collect_files(p, "java", files=["src/core.java"])
    assert [f.relative_to(p).as_posix() for f in files] == ["src/core.java"]


def test_collect_test_files(tmp_path):
    p = _proj(tmp_path, "java")
    tests = cf.collect_test_files(p, "java")
    rels = [f.relative_to(p).as_posix() for f in tests]
    assert "tests/CoreTest.java" in rels
    assert "src/core.java" not in rels


@pytest.mark.parametrize(
    ("lang", "path", "expected"),
    [
        ("java", "src/App.java", False),
        ("java", "tests/App.java", True),
        ("java", "src/AppTest.java", True),
        ("java", "src/TestApp.java", True),
        ("csharp", "src/App.cs", False),
        ("csharp", "Tests/App.cs", True),
        ("csharp", "src/AppTests.cs", True),
        ("csharp", "src/TestApp.cs", True),
        ("cpp", "src/math.cpp", False),
        ("cpp", "src/math_test.cpp", True),
        ("cpp", "tests/math.cpp", True),
        ("cpp", "src/test_math.cpp", True),
    ],
)
def test_is_test_file(lang, path, expected):
    assert cf._is_test_file(Path(path), lang) is expected


def test_detect_java(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert cf.detect_java(tmp_path) is True
    assert cf.detect_csharp(tmp_path) is False
    assert cf.detect_cpp(tmp_path) is False


def test_detect_csharp(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>", encoding="utf-8")
    assert cf.detect_csharp(tmp_path) is True
    assert cf.detect_java(tmp_path) is False


def test_detect_cpp(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    assert cf.detect_cpp(tmp_path) is True
    assert cf.detect_java(tmp_path) is False
    assert cf.detect_csharp(tmp_path) is False


def test_detect_false_on_plain_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert cf.detect_java(tmp_path) is False
    assert cf.detect_csharp(tmp_path) is False
    assert cf.detect_cpp(tmp_path) is False


@pytest.mark.parametrize(
    ("lang", "markers"),
    [
        ("java", ("pom.xml", "mvn -q test")),
        ("csharp", ("App.csproj", "dotnet test")),
        ("cpp", ("CMakeLists.txt", "ctest --output-on-failure")),
    ],
)
def test_default_test_command(tmp_path, lang, markers):
    (tmp_path / markers[0]).write_text("x", encoding="utf-8")
    assert cf.default_test_command(tmp_path, lang) == markers[1]


def test_default_test_command_gradle(tmp_path):
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    assert cf.default_test_command(tmp_path, "java") == "gradle test"


# ---------------------------------------------------------------------------
# CLI integration (fake command, no toolchain required)
# ---------------------------------------------------------------------------


def _java_project(tmp_path, exit_code: int) -> Path:
    calc = tmp_path / "calc"
    calc.mkdir()
    (calc / "Math.java").write_text(JAVA_SRC, encoding="utf-8")
    (tmp_path / ".mutation-gate.toml").write_text(
        'language = "java"\n'
        f'test_command = \'python -c "import sys;sys.exit({exit_code})"\'\n'
        "cache = false\n"
        "workers = 2\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_cli(args, cwd):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "mutation_gate.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
    )


def test_cli_run_java_all_killed(tmp_path):
    p = _java_project(tmp_path, exit_code=1)
    proc = _run_cli(["run", ".", "--ignore-baseline"], p)
    assert "Mutation score: 100.0%" in proc.stdout, proc.stdout + proc.stderr


def test_cli_run_java_all_survived(tmp_path):
    p = _java_project(tmp_path, exit_code=0)
    proc = _run_cli(["run", ".", "--ignore-baseline"], p)
    assert "Mutation score: 0.0%" in proc.stdout, proc.stdout + proc.stderr


def test_cli_mutate_java_suffix(tmp_path):
    p = _java_project(tmp_path, exit_code=1)
    out = tmp_path / "dump"
    proc = _run_cli(["mutate", ".", "--out", "dump"], p)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    names = [f.name for f in out.iterdir() if f.suffix == ".java"]
    assert names
    assert (out / "manifest.json").exists()


def test_cli_init_detects_language(tmp_path):
    calc = tmp_path / "calc"
    calc.mkdir()
    (calc / "Math.java").write_text(JAVA_SRC, encoding="utf-8")
    proc = _run_cli(["init", "."], tmp_path)
    assert proc.returncode == 0
    toml = (tmp_path / ".mutation-gate.toml").read_text(encoding="utf-8")
    assert 'language = "java"' in toml
    assert 'test_command = "mvn -q test"' in toml
    cfg = load_config(tmp_path)
    assert cfg.language == "java"


def test_cli_init_python_default(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.py").write_text("x = 1\n", encoding="utf-8")
    proc = _run_cli(["init", "."], tmp_path)
    assert proc.returncode == 0
    toml = (tmp_path / ".mutation-gate.toml").read_text(encoding="utf-8")
    assert 'language = "python"' in toml
    assert 'test_command = "pytest -q"' in toml


def test_cli_verify_java_uses_default_command(tmp_path, monkeypatch):
    from argparse import Namespace

    from mutation_gate import cli

    p = _java_project(tmp_path, exit_code=1)
    calls = {}

    def fake_verify(root, test_file, lang, cfg):
        calls["cmd"] = cfg.test_command
        calls["lang"] = lang
        from mutation_gate.model import VerifyResult

        return VerifyResult(
            test_file=test_file,
            reachable=2,
            killed=2,
            survived=0,
            invalid=0,
            survivors=[],
            coverage_available=False,
        )

    monkeypatch.setattr(cf, "verify_cfamily_project", fake_verify)
    args = Namespace(
        root=str(p),
        config=None,
        workers=None,
        timeout=None,
        operators=None,
        no_cache=True,
        test_file="calc/Math.java",
        json=None,
        gate=None,
    )
    rc = cli.cmd_verify(args)
    assert rc == 0
    assert calls["lang"] == "java"
    assert calls["cmd"] == 'python -c "import sys;sys.exit(1)"'


# ---------------------------------------------------------------------------
# Real-toolchain e2e (skipped when toolchain missing)
# ---------------------------------------------------------------------------


@needs_dotnet
def test_csharp_demo_run_e2e():
    proc = _run_cli(["run", ".", "--ignore-baseline", "--operators", "comparison"], DEMO_CSHARP)
    import re

    m = re.search(r"Mutation score:\s+([\d.]+)%", proc.stdout)
    assert m, proc.stdout + proc.stderr
    score = float(m.group(1)) / 100
    assert 0.0 < score < 1.0


@needs_dotnet
def test_csharp_verify_contribution_gate():
    proc = _run_cli(["verify", "Calc/Tests.cs", "--gate", "0.5", "--operators", "comparison"], DEMO_CSHARP)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Contribution" in proc.stdout or "contribution" in proc.stdout.lower()


@needs_dotnet
def test_csharp_detect_and_default_cmd():
    cfg = load_config(DEMO_CSHARP)
    assert cfg.language == "csharp"
    assert cf.default_test_command(DEMO_CSHARP, "csharp") == "dotnet test"
