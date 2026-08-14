"""Unit tests for mutant generation."""

from pathlib import Path

from mutation_gate.generate import generate_mutants


def test_bom_prefixed_source_still_mutates():
    src = "\ufeffdef f(a, b):\n    return a < b\n"
    mutants = generate_mutants(src, Path("m.py"))
    assert any(m.operator == "comparison" for m in mutants)


def test_comparison_flip_generated():
    src = "def f(a, b):\n    return a < b\n"
    mutants = generate_mutants(src, Path("m.py"))
    ops = {m.operator for m in mutants}
    assert "comparison" in ops
    assert any(m.after.strip() == "a <= b" for m in mutants if m.operator == "comparison")


def test_num_literal_increment():
    src = "def f():\n    return 5\n"
    mutants = generate_mutants(src, Path("m.py"))
    nums = {m.after.strip() for m in mutants if m.operator == "num_literal"}
    assert "6" in nums


def test_bool_literal_flip():
    src = "def f():\n    return True\n"
    mutants = generate_mutants(src, Path("m.py"))
    bools = {m.after.strip() for m in mutants if m.operator == "bool_literal"}
    assert "False" in bools


def test_negate_condition():
    src = "def f(x):\n    if x > 0:\n        return 1\n    return 0\n"
    mutants = generate_mutants(src, Path("m.py"))
    neg = [m for m in mutants if m.operator == "negate_condition"]
    assert neg
    assert "not" in neg[0].after


def test_return_none():
    src = "def f():\n    return 42\n"
    mutants = generate_mutants(src, Path("m.py"))
    rn = [m for m in mutants if m.operator == "return_none"]
    assert rn
    assert rn[0].after.strip() == "return"


def test_syntax_error_source_yields_no_mutants():
    mutants = generate_mutants("def f(:\n", Path("bad.py"))
    assert mutants == []


def test_each_mutant_is_valid_python():
    src = "def f(a):\n    if a < 10:\n        return a + 1\n    return 0\n"
    for m in generate_mutants(src, Path("m.py")):
        compile(m.source, "m.py", "exec")
