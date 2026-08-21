"""Swift target support (pure-Python tokenizer engine).

Swift shares enough C-family syntax to reuse the token-replacement approach,
but differs in three structural ways that a dedicated engine handles better
than parameterizing `cfamily.py`:

1. Conditions are not parenthesized (`if x > 5 {`), so negate-condition must
   find the condition extent itself and wrap it in `!(...)`.
2. Statements end at newlines, not semicolons, so statement removal groups
   tokens per line instead of triggering on `;`.
3. `!` is both prefix negation and postfix force-unwrap; `..<`/`...` range
   operators and `#"..."#` raw strings exist; block comments nest.

Like the C-family engine, mutations are in-place token replacements — only the
targeted token (or a bounded span) changes, so formatting/comments survive
byte-for-byte. A mutant that breaks compilation makes the test command exit
non-zero and is counted as killed; that is the standard approximation for
token-based mutation tools (PIT, Stryker filter at the parser level). Generic
parameters (`Foo<Bar>`) can therefore surface as compile-failed "comparison"
mutants — accepted noise, same as the C-family target.
"""

from __future__ import annotations

import hashlib
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from .cfamily import _apply_edits, _mutate_number
from .model import Mutant, VerifyResult

LANGUAGES = ("swift",)

SOURCE_EXTS = {
    "swift": {".swift"},
}

SWIFT_OPERATORS = [
    "comparison",
    "boolop",
    "binop",
    "aug_assign",
    "bool_literal",
    "num_literal",
    "str_literal",
    "remove_not",
    "negate_condition",
    "remove_stmt",
]

_NOISE = {
    ".git",
    ".hg",
    ".svn",
    ".vs",
    ".vscode",
    "node_modules",
    "target",
    "bin",
    "obj",
    "build",
    "out",
    "dist",
    "Debug",
    "Release",
    ".venv",
    "venv",
    "__pycache__",
    ".mutation-gate",
    "DerivedData",
    ".build",
    "Pods",
    ".swiftpm",
    ".xcuserdata",
}

_TEST_PARTS = {"test", "tests"}

_OPS = [
    "...", "..<",
    "===", "!==",
    "??=",
    "<<=", ">>=",
    "&+", "&-", "&*",
    "&&", "||",
    "<=", ">=", "==", "!=", "+=", "-=", "*=", "/=", "%=",
    "??", "?.", "->", "<<", ">>",
    "{", "}", "(", ")", "[", "]", ";", ",", ".", ":", "?", "<", ">",
    "+", "-", "*", "/", "%", "=", "&", "|", "^", "~", "!", "@", "#", "\\",
]
_OPS.sort(key=len, reverse=True)

_CMP_FLIP = {
    "==": "!=", "!=": "==",
    "<": "<=", "<=": "<",
    ">": ">=", ">=": ">",
    "===": "!==", "!==": "===",
}
_BINOP_FLIP = {"+": "-", "-": "+", "*": "/", "/": "*", "%": "/"}
_AUG_FLIP = {
    "+=": "-=", "-=": "+=",
    "*=": "/=", "/=": "*=",
    "%=": "/=",
}

# Keywords/contexts where a whole-line statement must never be deleted.
_BLOCKED_SWIFT = {
    "import", "func", "class", "struct", "enum", "protocol", "extension",
    "actor", "init", "deinit", "subscript", "operator",
    "if", "else", "guard", "while", "for", "switch", "case", "default",
    "do", "catch", "defer", "break", "continue",
    "public", "private", "fileprivate", "internal", "open", "final",
    "override", "required", "lazy", "weak", "unowned", "static",
    "mutating", "nonisolated", "where", "inout", "some", "any",
}


@dataclass
class _Tok:
    kind: str  # ws | comment | str | num | id | op
    text: str
    start: int
    end: int
    line: int
    depth: int   # open-paren nesting at token start
    bdepth: int  # open-brace nesting at token start
    prefixed: bool = False


def _prev_non_ws(toks: list[_Tok], idx: int | None = None) -> _Tok | None:
    if idx is None:
        idx = len(toks)
    for k in range(idx - 1, -1, -1):
        if toks[k].kind in ("ws", "comment"):
            continue
        return toks[k]
    return None


def _next_token(toks: list[_Tok], idx: int) -> tuple[int, _Tok] | None:
    for k in range(idx + 1, len(toks)):
        if toks[k].kind not in ("ws", "comment"):
            return k, toks[k]
    return None


def _scan_swift_number(source: str, i: int) -> int:
    """Consume a Swift numeric literal (hex/octal/binary, `_` separators)."""
    n = len(source)
    low = source.lower()
    if low.startswith("0x", i) or low.startswith("0b", i) or low.startswith("0o", i):
        j = i + 2
        while j < n and (source[j].isalnum() or source[j] == "_"):
            j += 1
        return j
    j = i
    while j < n and (source[j].isdigit() or source[j] == "_"):
        j += 1
    # Fraction only when a digit follows the dot (`1.foo` is not a float).
    if j < n and source[j] == "." and j + 1 < n and source[j + 1].isdigit():
        j += 1
        while j < n and (source[j].isdigit() or source[j] == "_"):
            j += 1
    if j < n and source[j] in "eE":
        k = j + 1
        if k < n and source[k] in "+-":
            k += 1
        if k < n and source[k].isdigit():
            j = k
            while j < n and source[j].isdigit():
                j += 1
    return j


def _tokenize(source: str) -> list[_Tok]:
    """Tokenize Swift source, preserving offsets, line numbers, and nesting."""
    toks: list[_Tok] = []
    i, n = 0, len(source)
    line, pdepth, bdepth = 1, 0, 0
    if source.startswith("\ufeff"):
        i = 1
    while i < n:
        c = source[i]

        # Whitespace
        if c in " \t\r\n":
            j = i
            while j < n and source[j] in " \t\r\n":
                if source[j] == "\n":
                    line += 1
                j += 1
            toks.append(_Tok("ws", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Line comment
        if source.startswith("//", i):
            j = source.find("\n", i)
            j = n if j == -1 else j
            toks.append(_Tok("comment", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Block comment (Swift block comments NEST)
        if source.startswith("/*", i):
            level, j = 1, i + 2
            while j < n and level:
                if source.startswith("/*", j):
                    level += 1
                    j += 2
                elif source.startswith("*/", j):
                    level -= 1
                    j += 2
                else:
                    if source[j] == "\n":
                        line += 1
                    j += 1
            toks.append(_Tok("comment", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Multiline string """..."""
        if source.startswith('"""', i):
            j = i + 3
            while j < n and not source.startswith('"""', j):
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == "\n":
                    line += 1
                j += 1
            j = min(j + 3, n)
            toks.append(_Tok("str", source[i:j], i, j, line, pdepth, bdepth, prefixed=True))
            i = j
            continue

        # Extended-delimiter string #"..."# / ##"..."##
        if c == "#":
            k = i
            while k < n and source[k] == "#":
                k += 1
            if k < n and source[k] == '"' and k - i <= 8:
                close = '"' + "#" * (k - i)
                j = k + 1
                while j < n and not source.startswith(close, j):
                    if source[j] == "\\":
                        j += 2
                        continue
                    if source[j] == "\n":
                        line += 1
                    j += 1
                j = min(j + len(close), n)
                toks.append(_Tok("str", source[i:j], i, j, line, pdepth, bdepth, prefixed=True))
                i = j
                continue
            # Otherwise: directive/pound op falls through to operator matching.

        # Plain string
        if c == '"':
            j = i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == '"':
                    break
                if source[j] == "\n":
                    line += 1
                j += 1
            j = min(j + 1, n)
            toks.append(_Tok("str", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Numbers
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            j = _scan_swift_number(source, i)
            toks.append(_Tok("num", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Identifiers (incl. backtick-escaped names)
        if c.isalpha() or c == "_" or c == "`":
            if c == "`":
                close = source.find("`", i + 1)
                j = n if close == -1 else close + 1
            else:
                j = i
                while j < n and (source[j].isalnum() or source[j] == "_"):
                    j += 1
            toks.append(_Tok("id", source[i:j], i, j, line, pdepth, bdepth))
            i = j
            continue

        # Operators (longest match)
        op = _match_op(source, i)
        text = op if op else c
        j = i + len(text)
        toks.append(_Tok("op", text, i, j, line, pdepth, bdepth))
        if text == "(":
            pdepth += 1
        elif text == ")":
            pdepth = max(0, pdepth - 1)
        elif text == "{":
            bdepth += 1
        elif text == "}":
            bdepth = max(0, bdepth - 1)
        i = j
    return toks


def _match_op(source: str, i: int) -> str | None:
    for op in _OPS:
        if source.startswith(op, i):
            return op
    return None


# ---------------------------------------------------------------------------
# Mutation sites
# ---------------------------------------------------------------------------

_CONTINUATION_TAILS = {
    ",", ".", "+", "-", "*", "/", "%", "&&", "||", "=", "->",
    "?", ":", "(", "==", "!=", "<", ">", "<=", ">=", "===", "!==", "..", "..<",
}


def _swift_remove_stmt_edits(toks: list[_Tok], idx: int) -> list[tuple[int, int, str]] | None:
    """Delete a single-line statement (newline-terminated, brace-nested).

    Only expression statements qualify: calls, assignments, `return expr`,
    `throw err`. Declarations (`let`/`var`/`func`…), control-flow headers,
    attributes/directives, closure-bearing lines, and multi-line statement
    prefixes are all skipped.
    """
    t0 = toks[idx]
    if t0.bdepth < 1 or t0.kind != "id" or t0.text in _BLOCKED_SWIFT:
        return None

    group = [t0]
    j = idx + 1
    while j < len(toks) and toks[j].line == t0.line:
        group.append(toks[j])
        j += 1

    if len(group) == 1:
        return None
    for tk in group:
        if tk.kind == "op" and tk.text in ("{", "}"):
            return None
        if tk.kind == "id" and tk.text in ("let", "var"):
            return None

    last = group[-1]
    if last.kind == "op" and last.text in _CONTINUATION_TAILS:
        return None  # statement continues on the next line

    has_assign = any(tk.kind == "op" and tk.text == "=" for tk in group)
    has_call = any(
        group[k].kind == "id"
        and group[k + 1].kind == "op"
        and group[k + 1].text == "("
        for k in range(len(group) - 1)
    )
    starts_flow = t0.text in ("return", "throw")
    if not (has_assign or has_call or starts_flow):
        return None

    return [(t0.start, last.end, "")]


def _negate_condition_edits(
    toks: list[_Tok], idx: int, source: str
) -> tuple[str, str, list[tuple[int, int, str]]] | None:
    """Wrap the first simple condition of if/guard/while in `!(...)`."""
    head = toks[idx]
    j = idx + 1
    if j >= len(toks):
        return None
    pdepth = head.depth
    end = None
    while j < len(toks):
        tk = toks[j]
        if tk.kind == "op":
            if tk.text == "(":
                pdepth += 1
            elif tk.text == ")":
                pdepth -= 1
            elif tk.text == "{" and pdepth == head.depth:
                end = j
                break
        elif tk.kind == "id":
            if head.text == "guard" and tk.text == "else" and pdepth == head.depth:
                end = j
                break
            if tk.text in ("let", "var"):
                return None  # condition bindings cannot be naively negated
            if tk.text in ("available", "unavailable"):
                return None  # availability conditions reject negation
        j += 1
    if end is None or end == idx + 1:
        return None

    cond_toks = toks[idx + 1:end]
    base_depth = cond_toks[0].depth
    for ct in cond_toks:
        if ct.kind == "op" and ct.text == "," and ct.depth == base_depth:
            return None  # condition list (`if a, b`) — not a single expression

    first, lastc = cond_toks[0], cond_toks[-1]
    cond = source[first.start:lastc.end]
    edits = [(first.start, first.start, "!("), (lastc.end, lastc.end, ")")]
    return cond, f"!({cond})", edits


def _sites(
    t: _Tok, toks: list[_Tok], idx: int, source: str
) -> list[tuple[str, str, str, list[tuple[int, int, str]]]]:
    out: list[tuple[str, str, str, list[tuple[int, int, str]]]] = []

    if t.kind == "op":
        if t.text in _CMP_FLIP:
            repl = _CMP_FLIP[t.text]
            out.append(("comparison", t.text, repl, [(t.start, t.end, repl)]))
        elif t.text in _BINOP_FLIP:
            if t.text == "*":
                prev = _prev_non_ws(toks, idx)
                # `*` must sit between operands: `a * b`, `2 * x`, `(a) * b`.
                if prev is not None and (
                    prev.kind in ("id", "num", "str") or prev.text in (")", "]")
                ):
                    out.append(("binop", t.text, _BINOP_FLIP[t.text], [(t.start, t.end, _BINOP_FLIP[t.text])]))
            else:
                out.append(("binop", t.text, _BINOP_FLIP[t.text], [(t.start, t.end, _BINOP_FLIP[t.text])]))
        elif t.text in _AUG_FLIP:
            out.append(("aug_assign", t.text, _AUG_FLIP[t.text], [(t.start, t.end, _AUG_FLIP[t.text])]))
        elif t.text in ("&&", "||"):
            repl = "||" if t.text == "&&" else "&&"
            out.append(("boolop", t.text, repl, [(t.start, t.end, repl)]))
        elif t.text == "!":
            nxt = _next_token(toks, idx)
            # Prefix negation is `!` directly followed by an identifier or `(`
            # ON THE SAME LINE; anything else (`opt!` at end of line, `x!.y`)
            # is postfix force-unwrap.
            if (
                nxt is not None
                and nxt[1].line == t.line
                and (nxt[1].kind == "id" or (nxt[1].kind == "op" and nxt[1].text == "("))
            ):
                out.append(("remove_not", t.text, "", [(t.start, t.end, "")]))

    elif t.kind == "id":
        if t.text in ("true", "false"):
            repl = "false" if t.text == "true" else "true"
            out.append(("bool_literal", t.text, repl, [(t.start, t.end, repl)]))
        elif t.text in ("if", "guard", "while"):
            nc = _negate_condition_edits(toks, idx, source)
            if nc is not None:
                before, after, edits = nc
                out.append(("negate_condition", before, after, edits))

        # Statements end at newlines in Swift: a line-starting identifier at
        # brace depth >= 1 is a candidate statement head.
        if idx == 0 or toks[idx - 1].line < t.line:
            edits = _swift_remove_stmt_edits(toks, idx)
            if edits:
                out.append(("remove_stmt", source[edits[0][0]:edits[0][1]], "", edits))

    elif t.kind == "num":
        new = _mutate_number(t.text)
        if new and new != t.text:
            out.append(("num_literal", t.text, new, [(t.start, t.end, new)]))

    elif t.kind == "str" and not t.prefixed:
        new = '""' if len(t.text) > 2 else '"MUTANT"'
        out.append(("str_literal", t.text, new, [(t.start, t.end, new)]))

    return out


def generate_swift_mutants(
    source: str,
    path: Path,
    lang: str = "swift",
    operators: list[str] | None = None,
) -> list[Mutant]:
    """One Mutant per (site, operator) producing a distinct file variant."""
    del lang  # kept for call-site symmetry with cfamily.generate_cfamily_mutants
    toks = [t for t in _tokenize(source) if t.kind not in ("ws", "comment")]
    if not toks:
        return []
    ops: set[str] | None = set(operators) if operators else None

    mutants: dict[str, Mutant] = {}
    for i, t in enumerate(toks):
        for op_name, before, after, edits in _sites(t, toks, i, source):
            if ops is not None and op_name not in ops:
                continue
            mutated = _apply_edits(source, edits)
            if mutated == source:
                continue
            key = hashlib.sha1(mutated.encode()).hexdigest()
            if key in mutants:
                continue
            mutants[key] = Mutant(
                id=0,
                file=path,
                lineno=t.line,
                operator=op_name,
                before=before,
                after=after,
                source=mutated,
                original=source,
            )
    result = list(mutants.values())
    for i, m in enumerate(result):
        m.id = i
    return result


# ---------------------------------------------------------------------------
# File discovery + project detection
# ---------------------------------------------------------------------------


def _is_test_file(rel: Path, lang: str = "swift") -> bool:
    del lang
    parts = rel.parts
    if any(part.lower() in _TEST_PARTS for part in parts[:-1]):
        return True
    stem = rel.stem.lower()
    return stem.startswith("test") or stem.endswith("tests") or stem.endswith("test")


def _is_manifest(rel: Path) -> bool:
    return rel.name == "Package.swift"


def _iter_source_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part in _NOISE for part in rel.parts):
            continue
        if p.suffix not in SOURCE_EXTS["swift"]:
            continue
        if _is_test_file(rel) or _is_manifest(rel):
            continue
        yield p


def collect_files(root: Path, lang: str = "swift", files: list[str] | None = None) -> list[Path]:
    """Source files to mutate (relative to root), optionally filtered by --files."""
    if files:
        wanted = {Path(f).as_posix() for f in files}
        return [
            p
            for p in sorted(_iter_source_files(root))
            if p.relative_to(root).as_posix() in wanted
        ]
    return sorted(_iter_source_files(root))


def collect_test_files(root: Path, lang: str = "swift") -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in SOURCE_EXTS["swift"]
        and not any(part in _NOISE for part in p.relative_to(root).parts)
        and _is_test_file(p.relative_to(root))
    )


def _bounded_hit(root: Path, globs: list[str]) -> bool:
    for pat in globs:
        if next(root.glob(pat), None) is not None:
            return True
    return False


def detect_swift(root: Path) -> bool:
    if (root / "Package.swift").exists():
        return True
    if _bounded_hit(root, ["*.xcodeproj", "*.xcworkspace"]):
        return True
    return _bounded_hit(root, ["Sources/**/*.swift", "**/*.swift"])


def default_test_command(root: Path, lang: str = "swift") -> str:
    del lang
    projects = sorted(root.glob("*.xcodeproj"))
    if projects:
        proj = projects[0]
        return f"xcodebuild test -project {shlex.quote(proj.name)} -scheme {shlex.quote(proj.stem)}"
    if (root / "Package.swift").exists():
        return "swift test"
    return "swift test"


# ---------------------------------------------------------------------------
# verify (per-test-file contribution)
# ---------------------------------------------------------------------------


def verify_swift_project(root: Path, test_file: Path, lang: str = "swift", cfg=None) -> VerifyResult:
    """Per-test-file contribution: run the suite restricted to that test file.

    Swift has no universally available per-file test filter (SPM runs the whole
    package; xcodebuild needs `<TestTarget>/<Class>` which we don't discover in
    v1), so like custom-command C-family targets this runs the FULL suite and
    reports a suite-wide contribution, with a warning printed.
    """
    from .runner import Runner

    del lang
    test_file = Path(test_file)
    if not test_file.is_absolute():
        test_file = root / test_file

    print(
        f"⚠️  No per-file test invocation for swift + '{cfg.test_command}'; "
        "verifying against the FULL suite (suite-wide contribution, not per-file).",
        file=sys.stderr,
    )

    mutants: list[Mutant] = []
    for f in collect_files(root):
        rel = f.relative_to(root)
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        mutants.extend(generate_swift_mutants(source, rel, operators=getattr(cfg, "operators", None)))

    cache_file = root / cfg.cache_file if getattr(cfg, "cache", False) else None
    runner = Runner(
        root,
        test_command=cfg.test_command,
        timeout=cfg.timeout,
        workers=cfg.workers,
        cache_file=cache_file,
    )
    results, _cached = runner.run(mutants)

    killed, survived = 0, 0
    survivors: list[Mutant] = []
    for r in results:
        if r.status == "killed":
            killed += 1
        elif r.status == "survived":
            survived += 1
            survivors.append(r.mutant)

    return VerifyResult(
        test_file=test_file,
        reachable=len(mutants),
        killed=killed,
        survived=survived,
        invalid=0,
        survivors=survivors,
        coverage_available=False,
    )
