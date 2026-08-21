"""Mutant generation: walk a module's AST, produce one variant per candidate site."""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

from .model import Mutant
from .operators import OPERATORS, prepare_tree

_MATCH = (ast.Module,)


def _iter_with_path(node: ast.AST, path: list[tuple[str, int | None]]):
    """Yield (node, path_to_node_from_root) for every node in the tree."""
    yield node, path
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            for i, child in enumerate(value):
                if isinstance(child, ast.AST):
                    yield from _iter_with_path(child, path + [(field, i)])
        elif isinstance(value, ast.AST):
            yield from _iter_with_path(value, path + [(field, None)])


def _navigate(tree: ast.AST, path: list[tuple[str, int | None]]) -> tuple[object, tuple[str, int | None]]:
    """Descend to the parent of the path target. Returns (parent, last_step)."""
    if not path:
        raise ValueError("empty path")
    node = tree
    for step in path[:-1]:
        field, index = step
        if index is None:
            node = getattr(node, field)
        else:
            node = getattr(node, field)[index]
    return node, path[-1]


def _replace_at(tree: ast.AST, path: list[tuple[str, int | None]], replacement: ast.AST) -> None:
    parent, (field, index) = _navigate(tree, path)
    if index is None:
        setattr(parent, field, replacement)
    else:
        getattr(parent, field)[index] = replacement


def generate_mutants(
    source: str,
    path: Path,
    operators: dict | None = None,
    mutate_docstrings: bool = False,
) -> list[Mutant]:
    """Return one Mutant per (site, operator) producing a distinct file variant."""
    ops = operators or OPERATORS
    if source.startswith("\ufeff"):
        source = source[1:]  # tolerate a UTF-8 BOM (common on Windows-authored files)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    prepare_tree(tree, mutate_docstrings)

    mutants: dict[str, Mutant] = {}
    for node, node_path in _iter_with_path(tree, []):
        if isinstance(node, _MATCH):
            continue
        # skip docstring Expr nodes themselves; also skip the constants inside them
        if getattr(node, "_mutate_skip", False):
            continue
        if isinstance(node, ast.Expr) and _is_docstring_expr(node) and not mutate_docstrings:
            continue
        for op_name, op_fn in ops.items():
            replacement = op_fn(node)
            if replacement is None:
                continue
            # Swap the mutant into the live tree, unparse, then swap the
            # original back. The old approach deep-copied the whole tree per
            # candidate, which dominated generation time on large files.
            try:
                parent, (field, index) = _navigate(tree, node_path)
                if index is None:
                    original = getattr(parent, field)
                    setattr(parent, field, replacement)
                else:
                    container = getattr(parent, field)
                    original = container[index]
                    container[index] = replacement
            except Exception:
                continue
            try:
                mutated_source = ast.unparse(tree)
                ast.parse(mutated_source)  # sanity
            except SyntaxError:
                mutated_source = ""
            finally:
                if index is None:
                    setattr(parent, field, original)
                else:
                    container[index] = original
            if not mutated_source or mutated_source == source:
                continue
            key = hashlib.sha1(mutated_source.encode()).hexdigest()
            if key in mutants:
                continue
            lineno = getattr(node, "lineno", 0)
            mutants[key] = Mutant(
                id=0,
                file=path,
                lineno=lineno,
                operator=op_name,
                before=ast.unparse(node),
                after=ast.unparse(replacement),
                source=mutated_source,
                original=source,
            )
    result = list(mutants.values())
    for i, m in enumerate(result):
        m.id = i
    return result


def _is_docstring_expr(node: ast.Expr) -> bool:
    """True if node is the first statement of a body and a bare string."""
    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
        return False
    parent = getattr(node, "_parent", None)
    for cls in (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef):
        if isinstance(parent, cls) and getattr(parent, "body", []) and parent.body[0] is node:
            return True
    return False
