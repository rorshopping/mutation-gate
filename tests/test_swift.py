"""Swift target tests (pure-Python tokenizer engine)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mutation_gate import swift as sw
from mutation_gate.config import load_config

REPO = Path(__file__).resolve().parents[1]
DEMO_SWIFT = REPO / "examples" / "demo-swift"

_HAS_SWIFT = shutil.which("swift") is not None

needs_swift = pytest.mark.skipif(not _HAS_SWIFT, reason="needs Swift toolchain")

SWIFT_SRC = """\
import Foundation

enum Math {
    static let limit: Int = 10

    static func clamp(_ v: Int) -> Int {
        if v < 0 { return 0 }
        if v > limit { return limit }
        return v
    }

    static func isZero(_ x: Int) -> Bool {
        return !(x != 0)
    }

    static func enabled(defaults: Bool) -> Bool {
        var flag = false
        if defaults {
            flag = true
        }
        return flag
    }

    static func grade(_ score: Double) -> String {
        if score >= 90 && score <= 100 { return "A" }
        guard score > 0 else { return "F" }
        while score < 1 { score += 1 }
        return "B"
    }

    /// Raw strings and ranges must not break the tokenizer.
    static func pattern(_ parts: [String]) -> String {
        let raw = #"line \\d+"#
        var out = ""
        for i in 0..<parts.count {
            out += parts[i]
            if i != parts.count - 1 {
                out += ","
            }
        }
        return raw + out
    }
}
"""


def ops_of(mutants):
    return [m.operator for m in mutants]


def by_op(mutants, op):
    return [m for m in mutants if m.operator == op]


# ---------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------


def test_tokenizer_kinds_and_offsets():
    toks = [t for t in sw._tokenize('let x = "hi" // note\n') if t.kind not in ("ws",)]
    kinds = [(t.kind, t.text) for t in toks]
    assert ("id", "let") in kinds
    assert ("str", '"hi"') in kinds
    assert ("comment", "// note") in kinds


def test_tokenizer_tracks_brace_depth():
    toks = [t for t in sw._tokenize("struct S {\n    let a = 1\n}\n") if t.kind not in ("ws",)]
    body_line = [t for t in toks if t.line == 2][0]
    assert body_line.bdepth == 1


def test_extended_and_multiline_strings_are_prefixed():
    src = 'let a = #"raw"#\nlet b = """\nmulti\n"""\n'
    strs = [t for t in sw._tokenize(src) if t.kind == "str"]
    assert len(strs) == 2
    assert all(t.prefixed for t in strs)


def test_range_operators_are_single_tokens():
    toks = [t for t in sw._tokenize("for i in 0..<4 {}\nif case 1...3 {}") if t.kind == "op"]
    texts = [t.text for t in toks]
    assert "..<" in texts and "..." in texts
    # `<` inside `..<` must not appear as its own token
    assert texts.count("<") == 0


def test_nested_block_comments():
    src = "/* outer /* inner */ still comment */ let x = 1\n"
    comments = [t for t in sw._tokenize(src) if t.kind == "comment"]
    assert len(comments) == 1
    assert comments[0].text.endswith("still comment */")


# ---------------------------------------------------------
# Mutant generation
# ---------------------------------------------------------


def test_generate_swift_mutants_basic_set():
    mutants = sw.generate_swift_mutants(SWIFT_SRC, Path("Math.swift"))
    ops = set(ops_of(mutants))
    assert {
        "comparison", "boolop", "binop", "aug_assign", "bool_literal",
        "num_literal", "str_literal", "remove_not", "negate_condition",
        "remove_stmt",
    } <= ops


def test_comparison_flip_includes_identity():
    src = "func eq(_ a: AnyObject, _ b: AnyObject) -> Bool { return a === b }\n"
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "comparison")
    assert any(m.before == "===" for m in ms)
    assert any(m.after == "!==" for m in ms)


def test_negate_condition_handles_paren_less_if():
    src = "func f(_ v: Int) -> Int {\n    if v < 0 { return 0 }\n    return v\n}\n"
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "negate_condition")
    assert len(ms) == 1
    assert ms[0].before == "v < 0"
    assert ms[0].after == "!(v < 0)"
    mutated = ms[0].source
    assert "if !(v < 0) {" in mutated


def test_negate_condition_guard_wraps_before_else():
    src = "func f(_ s: Int) -> String {\n    guard s > 0 else { return \"neg\" }\n    return \"pos\"\n}\n"
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "negate_condition")
    assert len(ms) == 1
    assert "guard !(s > 0) else {" in ms[0].source
    # `else` itself must remain untouched
    assert "else" in ms[0].source.split("!(")[1]


def test_negate_condition_skips_condition_lists_and_bindings():
    src = (
        "func f(a: Int?, b: Int) {\n"
        "    if let x = a, b > 0 { print(x) }\n"
        "    if b > 0, a != nil { print(1) }\n"
        "    if #available(iOS 17, *) { print(2) }\n"
        "}\n"
    )
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "negate_condition")
    assert ms == []


def test_remove_not_only_prefix_bang():
    src = (
        "func f(flag: Bool, opt: Int?) -> Int? {\n"
        "    if !flag { return nil }\n"
        "    let forced = opt!\n"
        "    return !(flag ? nil : opt)\n"
        "}\n"
    )
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "remove_not")
    lines = {m.lineno for m in ms}
    assert lines == {2, 4}  # prefix negations removed, on both lines
    # Line 2's mutants must keep the force-unwrap `opt!` intact.
    assert all("opt!" in m.source for m in ms if m.lineno == 2)
    # Line 4's mutant removes only the outer negation.
    line4 = [m for m in ms if m.lineno == 4]
    assert any(m.after == "" and m.before == "!" for m in line4)


def test_remove_stmt_deletes_calls_assignments_and_returns():
    src = (
        "class C {\n"
        "    var log: [String] = []\n"
        "    private let quota = 3\n"
        "    func run(_ n: Int) -> Int {\n"
        "        log.append(\"run\")\n"
        "        let doubled = n * 2\n"
        "        return doubled\n"
        "    }\n"
        "}\n"
    )
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "remove_stmt")
    removed = [m.before for m in ms]
    assert 'log.append("run")' in removed
    assert "return doubled" in removed
    # Declarations are never removed.
    assert not any("quota" in r or "doubled = n" in r or "log: [String]" in r for r in removed)


def test_remove_stmt_skips_multiline_statement_prefixes():
    src = (
        "class C {\n"
        "    func run() {\n"
        "        save(\n"
        "            name,\n"
        "            count\n"
        "        )\n"
        "    }\n"
        "}\n"
    )
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "remove_stmt")
    # `save(` ends with `(` — statement continues; nothing safely removable.
    assert ms == []


def test_string_literals_become_empty():
    src = 'func f() -> String { return "hello" }\n'
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "str_literal")
    assert len(ms) == 1 and ms[0].after == '""'


def test_raw_strings_are_not_mutated():
    src = 'let r = #"no mutate"#\n'
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "str_literal")
    assert ms == []


def test_numbers_increment_preserving_base():
    src = "let h = 0x1F\nlet d = 42\nlet f = 1.5\n"
    ms = by_op(sw.generate_swift_mutants(src, Path("X.swift")), "num_literal")
    afters = {m.after for m in ms}
    assert "0x20" in afters
    assert "43" in afters
    assert "2.5" in afters


def test_dedup_identical_variants():
    mutants = sw.generate_swift_mutants(SWIFT_SRC, Path("Math.swift"))
    hashes = {hashlib_sha(m.source) for m in mutants}
    assert len(hashes) == len(mutants)


def hashlib_sha(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode()).hexdigest()


def test_operator_filter():
    only_boolop = sw.generate_swift_mutants(SWIFT_SRC, Path("M.swift"), operators=["boolop"])
    assert {m.operator for m in only_boolop} == {"boolop"}


# ---------------------------------------------------------
# File discovery + detection
# ---------------------------------------------------------


def test_collect_files_excludes_tests(tmp_path: Path):
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Tests").mkdir()
    (tmp_path / "Sources" / "Core.swift").write_text("let a = 1\n")
    (tmp_path / "Tests" / "CoreTests.swift").write_text("import XCTest\n")
    files = sw.collect_files(tmp_path, "swift")
    assert [f.name for f in files] == ["Core.swift"]
    tests = sw.collect_test_files(tmp_path, "swift")
    assert [f.name for f in tests] == ["CoreTests.swift"]


def test_is_test_file_conventions():
    assert sw._is_test_file(Path("Tests/SleepMathTests.swift"))
    assert sw._is_test_file(Path("FooTests/CoreTest.swift"))
    assert not sw._is_test_file(Path("Sources/Engine.swift"))


def test_detect_swift_package_and_xcodeproj(tmp_path: Path):
    assert not sw.detect_swift(tmp_path)
    (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9\n")
    assert sw.detect_swift(tmp_path)

    proj_root = tmp_path / "proj"
    proj_root.mkdir()
    (proj_root / "App.xcodeproj").mkdir()
    assert sw.detect_swift(proj_root)

    src_root = tmp_path / "src-only"
    (src_root / "Sources").mkdir(parents=True)
    (src_root / "Sources" / "a.swift").write_text("let x = 1\n")
    assert sw.detect_swift(src_root)


def test_default_test_command(tmp_path: Path):
    (tmp_path / "App.xcodeproj").mkdir()
    cmd = sw.default_test_command(tmp_path)
    assert cmd.startswith("xcodebuild test -project App.xcodeproj -scheme App")

    pkg_root = tmp_path / "pkg"
    pkg_root.mkdir()
    (pkg_root / "Package.swift").write_text("// swift\n")
    assert sw.default_test_command(pkg_root) == "swift test"


def test_config_language_swift_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / ".mutation-gate.toml"
    cfg_file.write_text('language = "swift"\ntest_command = "swift test"\n', encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.language == "swift"


# ---------------------------------------------------------
# End-to-end against the demo package (needs the Swift toolchain)
# ---------------------------------------------------------


@needs_swift
def test_demo_swift_package_builds_and_produces_mutants(tmp_path: Path):
    result = subprocess.run(
        ["swift", "test"], cwd=DEMO_SWIFT,
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    cfg = load_config(DEMO_SWIFT)
    assert cfg.language == "swift"

    from mutation_gate.cli import _load_mutants

    class Args:
        files = None
        diff = False
        coverage_guided = False

    mutants = _load_mutants(DEMO_SWIFT, cfg, Args(), "swift")
    assert mutants, "expected mutants from the demo Swift package"
    # clamp/isZero/label logic must produce condition and operator mutants.
    ops = set(ops_of(mutants))
    assert {"comparison", "negate_condition", "remove_not"} <= ops
