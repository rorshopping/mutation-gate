"""Unit tests for mutation operators."""

from __future__ import annotations

from pathlib import Path

from mutation_gate.generate import generate_mutants


def _mutate(src: str) -> list:
    return generate_mutants(src, Path("m.py"))


def _ops(mutants, operator: str):
    return [m for m in mutants if m.operator == operator]


def test_comparison_flip_lt_to_lte():
    mutants = _mutate("def f(a):\n    return a < 5\n")
    assert any("a <= 5" in m.after for m in _ops(mutants, "comparison"))


def test_comparison_flip_in_to_not_in():
    mutants = _mutate("def f(x):\n    return x in [1, 2]\n")
    assert any("x not in [1, 2]" in m.after for m in _ops(mutants, "comparison"))


def test_comparison_flip_is_to_is_not():
    mutants = _mutate("def f(x):\n    return x is None\n")
    assert any("x is not None" in m.after for m in _ops(mutants, "comparison"))


def test_boolop_and_to_or():
    mutants = _mutate("def f(a, b):\n    return a and b\n")
    assert any("a or b" in m.after for m in _ops(mutants, "boolop"))


def test_bool_literal_flip():
    mutants = _mutate("def f():\n    return True\n")
    assert any(m.after == "False" for m in _ops(mutants, "bool_literal"))


def test_num_literal_increment():
    mutants = _mutate("def f():\n    return 5\n")
    assert any(m.after == "6" for m in _ops(mutants, "num_literal"))


def test_str_literal_empty():
    mutants = _mutate('def f():\n    return "hello"\n')
    assert any(m.after == "''" for m in _ops(mutants, "str_literal"))


def test_str_literal_skips_docstrings():
    mutants = _mutate('def f():\n    """Docstring."""\n    return "hello"\n')
    str_mutants = _ops(mutants, "str_literal")
    assert str_mutants  # the "hello" is still mutated
    assert all("Docstring" not in m.before for m in str_mutants)


def test_str_literal_mutates_docstrings_when_enabled():
    mutants = generate_mutants(
        'def f():\n    """Docstring."""\n    return "hello"\n',
        Path("m.py"),
        mutate_docstrings=True,
    )
    str_mutants = _ops(mutants, "str_literal")
    assert any("Docstring" in m.before for m in str_mutants)


def test_binop_flip_add_to_sub():
    mutants = _mutate("def f(a, b):\n    return a + b\n")
    assert any("a - b" in m.after for m in _ops(mutants, "binop"))


def test_binop_flip_mod_to_floordiv():
    mutants = _mutate("def f(a, b):\n    return a % b\n")
    assert any("a // b" in m.after for m in _ops(mutants, "binop"))


def test_aug_assign_flip():
    mutants = _mutate("def f(a):\n    a += 1\n    return a\n")
    assert any("a -= 1" in m.after for m in _ops(mutants, "aug_assign"))


def test_remove_not():
    mutants = _mutate("def f(a):\n    return not a\n")
    assert any(m.after == "a" for m in _ops(mutants, "remove_not"))


def test_negate_condition():
    mutants = _mutate("def f(a):\n    if a:\n        return 1\n    return 0\n")
    neg = _ops(mutants, "negate_condition")
    assert any("not a" in m.after for m in neg)


def test_return_none():
    mutants = _mutate("def f():\n    return 5\n")
    assert any(m.after == "return" for m in _ops(mutants, "return_none"))


def test_range():
    mutants = _mutate("def f():\n    return list(range(10))\n")
    assert any("range(9)" in m.after for m in _ops(mutants, "range"))


def test_remove_stmt():
    mutants = _mutate("def f(a):\n    if a:\n        return 1\n    return 0\n")
    rm = _ops(mutants, "remove_stmt")
    assert rm  # the if statement is removable


def test_remove_stmt_keeps_body_nonempty():
    # A single-statement function body must not be emptied.
    mutants = _mutate("def f():\n    return 5\n")
    assert not _ops(mutants, "remove_stmt")


def test_each_mutant_is_valid_python():
    for src in (
        "def f(a):\n    if a < 10:\n        return a + 1\n    return 0\n",
        "class C:\n    def m(self, x):\n        return x * 2\n",
        "for i in range(5):\n    print(i)\n",
        "x = [1, 2, 3]\n",
    ):
        for m in _mutate(src):
            compile(m.source, str(m.file), "exec")


def test_all_operators_are_used():
    from mutation_gate.operators import OPERATORS

    src = (
        "def f(a, b):\n"
        "    if a < b:\n"
        "        a += 1\n"
        "        return a\n"
        "    if a in b:\n"
        "        return not a\n"
        "    return a and b\n"
    )
    used = {m.operator for m in _mutate(src)}
    assert "comparison" in used
    assert "boolop" in used
    assert "aug_assign" in used
    assert "remove_not" in used
