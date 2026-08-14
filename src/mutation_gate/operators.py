"""AST mutation operators.

Each operator is a function: given an AST node, return a NEW node to substitute,
or None if the operator does not apply at that site.
"""

from __future__ import annotations

import ast
import copy

# BinaryOp replacement pairs.
_BINOP_FLIP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
    ast.Mod: ast.FloorDiv,
    ast.FloorDiv: ast.Mod,
    ast.BitOr: ast.BitAnd,
    ast.BitAnd: ast.BitOr,
}

# Augmented-assign replacement pairs.
_AUG_FLIP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
    ast.Mod: ast.FloorDiv,
}

# Statements that are safe to delete wholesale (their removal still leaves
# a valid module and tends to be behaviorally significant).
_REMOVABLE_STMTS = (
    ast.Return,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Expr,
    ast.Raise,
    ast.Assert,
    ast.If,
    ast.While,
    ast.For,
    ast.With,
    ast.Try,
    ast.Break,
    ast.Continue,
    ast.Delete,
)


_MUTATE_DOCSTRINGS = False


def _set_parents(tree: ast.AST) -> None:
    for child in ast.walk(tree):
        for field, value in ast.iter_fields(child):
            if isinstance(value, ast.AST):
                value._parent = child  # type: ignore[attr-defined]
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        item._parent = child  # type: ignore[attr-defined]


def _in_docstring(node: ast.AST) -> bool:
    """Walk up parents; True if this string literal is inside a docstring."""
    p = getattr(node, "_parent", None)
    if isinstance(p, ast.Expr) and p.value is node:
        # It's the value of a bare string Expr — only a docstring if that
        # Expr is the first statement of a body.
        gp = getattr(p, "_parent", None)
        for cls in (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef):
            if isinstance(gp, cls) and getattr(gp, "body", []) and gp.body[0] is p:
                return True
    return False


def op_comparison(node: ast.AST) -> ast.AST | None:
    """Flip a comparison operator: < ↔ <=, > ↔ >=, == ↔ !=, is ↔ is not, in ↔ not in."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    old = node.ops[0]
    mapping = {
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.Is: ast.IsNot,
        ast.IsNot: ast.Is,
        ast.In: ast.NotIn,
        ast.NotIn: ast.In,
    }
    new_cls = mapping.get(type(old))
    if new_cls is None:
        return None
    new = copy.deepcopy(node)
    new.ops = [new_cls()]
    return new


def op_boolop(node: ast.AST) -> ast.AST | None:
    """Flip and ↔ or."""
    if not isinstance(node, ast.BoolOp):
        return None
    new = copy.deepcopy(node)
    new.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    return new


def op_bool_literal(node: ast.AST) -> ast.AST | None:
    """Flip True ↔ False."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        new = copy.deepcopy(node)
        new.value = not node.value
        return new
    return None


def op_num_literal(node: ast.AST) -> ast.AST | None:
    """Numeric literal: n → n+1 (0 → 1; 0.0 → 1.0)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        new = copy.deepcopy(node)
        new.value = node.value + 1
        return new
    return None


def op_str_literal(node: ast.AST) -> ast.AST | None:
    """String literal: empty ↔ non-empty. Skips docstrings unless enabled."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if not _MUTATE_DOCSTRINGS and _in_docstring(node):
            return None
        new = copy.deepcopy(node)
        new.value = "" if node.value else "MUTANT"
        return new
    return None


def op_binop(node: ast.AST) -> ast.AST | None:
    """Flip binary operators: + ↔ -, * ↔ //, % ↔ //, | ↔ &."""
    if isinstance(node, ast.BinOp):
        new_cls = _BINOP_FLIP.get(type(node.op))
        if new_cls is None:
            return None
        new = copy.deepcopy(node)
        new.op = new_cls()
        return new
    return None


def op_aug_assign(node: ast.AST) -> ast.AST | None:
    """Flip augmented assignment: += ↔ -=, *= ↔ //=, %= ↔ //=."""
    if isinstance(node, ast.AugAssign):
        new_cls = _AUG_FLIP.get(type(node.op))
        if new_cls is None:
            return None
        new = copy.deepcopy(node)
        new.op = new_cls()
        return new
    return None


def op_remove_not(node: ast.AST) -> ast.AST | None:
    """Drop a unary `not`: not x → x."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return copy.deepcopy(node.operand)
    return None


def op_negate_condition(node: ast.AST) -> ast.AST | None:
    """Wrap the test of if/while in `not`."""
    if isinstance(node, (ast.If, ast.While, ast.Assert)):
        new = copy.deepcopy(node)
        new.test = ast.UnaryOp(op=ast.Not(), operand=copy.deepcopy(node.test))
        return new
    return None


def op_return_none(node: ast.AST) -> ast.AST | None:
    """return X → return None."""
    if isinstance(node, ast.Return) and node.value is not None:
        new = copy.deepcopy(node)
        new.value = None
        return new
    return None


def op_range(node: ast.AST) -> ast.AST | None:
    """range(n) → range(n-1); range(a, b) → range(a, b-1)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range"):
        return None
    if len(node.args) not in (1, 2):
        return None
    last = node.args[-1]
    if not (isinstance(last, ast.Constant) and isinstance(last.value, int)):
        return None
    new = copy.deepcopy(node)
    new.args[-1] = ast.Constant(value=last.value - 1)
    return new


def op_remove_stmt(node: ast.AST) -> ast.AST | None:
    """Delete a removable statement from a body (keeps at least one stmt)."""
    if not isinstance(node, _REMOVABLE_STMTS):
        return None
    parent = getattr(node, "_parent", None)
    if parent is None:
        return None
    for field, value in ast.iter_fields(parent):
        if isinstance(value, list) and any(item is node for item in value) and len(value) > 1:
            # A body with >1 statements: deletion is safe syntactically.
            return ast.Pass()
    return None


OPERATORS: dict[str, callable] = {
    "comparison": op_comparison,
    "boolop": op_boolop,
    "bool_literal": op_bool_literal,
    "num_literal": op_num_literal,
    "str_literal": op_str_literal,
    "binop": op_binop,
    "aug_assign": op_aug_assign,
    "remove_not": op_remove_not,
    "negate_condition": op_negate_condition,
    "return_none": op_return_none,
    "range": op_range,
    "remove_stmt": op_remove_stmt,
}


def prepare_tree(tree: ast.AST, mutate_docstrings: bool) -> ast.AST:
    """Attach parent links (needed by docstring/statement operators)."""
    global _MUTATE_DOCSTRINGS
    _MUTATE_DOCSTRINGS = mutate_docstrings
    _set_parents(tree)
    return tree
