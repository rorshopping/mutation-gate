"""Java / C# / C++ target support.

The three languages share C-family syntax, so one pure-Python tokenizer-based
engine covers all of them with zero dependencies. Mutations are *in-place token
replacements* — only the targeted token (or a bounded run) is changed, so the
rest of the file (formatting, comments) is preserved byte-for-byte.

Unlike the Python target we cannot `compile()` mutants to filter the invalid
ones; a C-family mutant that breaks compilation makes the test command exit
non-zero and is therefore counted as killed. This is the standard approximation
for token-based mutation tools (PIT, Stryker filter at the parser level).
"""

from __future__ import annotations

import hashlib
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .model import Mutant, VerifyResult

LANGUAGES = ("java", "csharp", "cpp")

SOURCE_EXTS = {
    "java": {".java"},
    "csharp": {".cs"},
    "cpp": {".cpp", ".cc", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx", ".inc", ".inl"},
}

# Operators shared with the Python/JS targets (subset of the same names).
CFAMILY_OPERATORS = [
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
    "x64",
    "x86",
    ".venv",
    "venv",
    "__pycache__",
    ".mutation-gate",
}

_TEST_PARTS = {"test", "tests", "spec", "specs"}

# Longest-match operator/punctuation table (sorted by length at runtime).
_OPS = [
    ">>>>>",
    ">>>>",
    ">>>=",
    ">>>",
    "<<=",
    ">>=",
    "<=>",
    "...",
    "??=",
    "??",
    "?.",
    "->",
    "=>",
    "++",
    "--",
    "&&",
    "||",
    "<<",
    ">>",
    "<=",
    ">=",
    "==",
    "!=",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "::",
    "(",
    ")",
    "{",
    "}",
    "[",
    "]",
    ";",
    ",",
    ".",
    ":",
    "?",
    "<",
    ">",
    "+",
    "-",
    "*",
    "/",
    "%",
    "=",
    "&",
    "|",
    "^",
    "~",
    "!",
    "@",
    "$",
    "#",
    "\\",
]
_OPS.sort(key=len, reverse=True)

_CMP_FLIP = {"==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_BINOP_FLIP = {"+": "-", "-": "+", "*": "/", "/": "*", "%": "/"}
_AUG_FLIP = {
    "+=": "-=",
    "-=": "+=",
    "*=": "/=",
    "/=": "*=",
    "%=": "/=",
    "&=": "|=",
    "|=": "&=",
    "^=": "|=",
    "<<=": ">>=",
    ">>=": "<<=",
}

# A statement whose first/any token is one of these is never removed.
_BLOCKED_STMT = {
    "if",
    "else",
    "while",
    "for",
    "do",
    "switch",
    "case",
    "default",
    "try",
    "catch",
    "class",
    "struct",
    "union",
    "enum",
    "namespace",
    "import",
    "package",
    "using",
}

# Type-like keywords: a line starting with one of these is a declaration
# unless it assigns a value, so remove_stmt skips pure declarations.
_TYPE_KEYWORDS = {
    "int",
    "long",
    "double",
    "float",
    "char",
    "bool",
    "void",
    "unsigned",
    "signed",
    "short",
    "const",
    "constexpr",
    "auto",
    "volatile",
    "size_t",
    "ssize_t",
    "byte",
    "object",
    "string",
    "var",
}

_CPP_STRING_PREFIXES = {"R", "u8", "u", "U", "L", "T"}
_CSHARP_STRING_PREFIXES = {"@", "$"}


@dataclass
class _Tok:
    kind: str  # ws | comment | directive | str | char | num | id | op
    text: str
    start: int
    end: int
    line: int
    depth: int
    prefixed: bool = False


def _at_line_start(source: str, i: int) -> bool:
    j = i
    while j > 0 and source[j - 1] in " \t":
        j -= 1
    return j == 0 or source[j - 1] == "\n"


def _scan_plain_string(source: str, i: int) -> tuple[int, bool]:
    j = i + 1
    n = len(source)
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1, True
        j += 1
    return n, False


def _scan_verbatim_string(source: str, i: int) -> tuple[int, bool]:
    """C# verbatim/interpolated strings: no backslash escapes, `""` is a quote."""
    j = i + 1
    n = len(source)
    while j < n:
        if source[j] == '"':
            if j + 1 < n and source[j + 1] == '"':
                j += 2
                continue
            return j + 1, True
        j += 1
    return n, False


def _scan_raw_triple(source: str, i: int) -> tuple[int, bool]:
    """C# 11 raw string literal (three double quotes)."""
    k = source.find('"""', i + 3)
    if k == -1:
        return len(source), False
    return k + 3, True


def _scan_cpp_raw(source: str, i: int) -> tuple[int, bool]:
    """C++ raw string `R"delim( ... )delim"` (source[i] == '"')."""
    n = len(source)
    j = i + 1
    k = j
    while k < n and (source[k].isalnum() or source[k] == "_"):
        k += 1
    if k >= n or source[k] != "(":
        return n, False
    delim = source[j:k]
    endmark = ")" + delim + '"'
    p = source.find(endmark, k + 1)
    if p == -1:
        return n, False
    return p + len(endmark), True


def _scan_char(source: str, i: int) -> tuple[int, bool]:
    n = len(source)
    j = i + 1
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            return j + 1, True
        if c in "\n\r":
            return j, False
        j += 1
    return n, False


def _prev_non_ws(toks: list[_Tok], idx: int | None = None) -> _Tok | None:
    """Previous non-ws/comment token, or None. When idx is given, only look before it."""
    if idx is None:
        idx = len(toks)
    for k in range(idx - 1, -1, -1):
        if toks[k].kind in ("ws", "comment"):
            continue
        return toks[k]
    return None


def _prev_any(toks: list[_Tok]) -> _Tok | None:
    for t in reversed(toks):
        if t.kind in ("ws", "comment"):
            continue
        return t
    return None


def _tokenize(source: str, lang: str) -> list[_Tok]:
    """Tokenize a C-family source file, preserving offsets and line numbers.

    `depth` on each token is the open-paren nesting depth at its start (used to
    keep statement-removal away from `for` headers and other parenthesized
    statements).
    """
    n = len(source)
    toks: list[_Tok] = []
    i = 0
    line = 1
    depth = 0
    while i < n:
        c = source[i]
        if c in " \t\r\n":
            j = i
            while j < n and source[j] in " \t\r\n":
                if source[j] == "\n":
                    line += 1
                j += 1
            toks.append(_Tok("ws", source[i:j], i, j, line, depth))
            i = j
            continue
        if c == "\ufeff":
            toks.append(_Tok("ws", source[i : i + 3], i, i + 3, line, depth))
            i += 3
            continue
        if source.startswith("//", i):
            j = source.find("\n", i)
            if j == -1:
                j = n
            toks.append(_Tok("comment", source[i:j], i, j, line, depth))
            i = j
            continue
        if source.startswith("/*", i):
            j = source.find("*/", i + 2)
            end = n if j == -1 else j + 2
            line += source[i:end].count("\n")
            toks.append(_Tok("comment", source[i:end], i, end, line, depth))
            i = end
            continue
        if c == "#" and _at_line_start(source, i):
            j = source.find("\n", i)
            if j == -1:
                j = n
            toks.append(_Tok("directive", source[i:j], i, j, line, depth))
            i = j
            continue
        if c == '"':
            prev = _prev_non_ws(toks)
            prev_text = prev.text if prev else None
            start_line = line
            if lang == "cpp" and prev_text == "R":
                end, ok = _scan_cpp_raw(source, i)
            elif lang == "csharp" and source.startswith('"""', i):
                end, ok = _scan_raw_triple(source, i)
            elif lang == "csharp" and prev_text in ("@", "$"):
                end, ok = _scan_verbatim_string(source, i)
            else:
                end, ok = _scan_plain_string(source, i)
            line += source[i:end].count("\n")
            prefixed = (lang == "cpp" and prev_text in _CPP_STRING_PREFIXES) or (
                lang == "csharp" and prev_text in _CSHARP_STRING_PREFIXES
            )
            toks.append(_Tok("str", source[i:end], i, end, start_line, depth, prefixed=prefixed))
            i = end
            continue
        if c == "'":
            start_line = line
            end, ok = _scan_char(source, i)
            if ok:
                line += source[i:end].count("\n")
                toks.append(_Tok("char", source[i:end], i, end, start_line, depth))
                i = end
                continue
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            j = _scan_number(source, i)
            toks.append(_Tok("num", source[i:j], i, j, line, depth))
            i = j
            continue
        if c.isalpha() or c in "_$":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            toks.append(_Tok("id", source[i:j], i, j, line, depth))
            i = j
            continue
        op = _match_op(source, i)
        if op is not None:
            toks.append(_Tok("op", op, i, i + len(op), line, depth))
            if op == "(":
                depth += 1
            elif op == ")":
                depth = max(0, depth - 1)
            i += len(op)
            continue
        toks.append(_Tok("op", c, i, i + 1, line, depth))
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        i += 1
    return toks


def _scan_number(source: str, i: int) -> int:
    n = len(source)
    j = i
    if source[i] == "0" and i + 1 < n and source[i + 1] in "xXbBoO":
        j = i + 2
        while j < n and (source[j].isalnum() or source[j] in "._'"):
            j += 1
        return j
    while j < n and (source[j].isdigit() or source[j] in "_'"):
        j += 1
    if j < n and source[j] == ".":
        j += 1
        while j < n and (source[j].isdigit() or source[j] == "_"):
            j += 1
    if j < n and source[j] in "eE":
        j += 1
        if j < n and source[j] in "+-":
            j += 1
        while j < n and (source[j].isdigit() or source[j] == "_"):
            j += 1
    while j < n and source[j].isalpha():
        j += 1
    return j


def _match_op(source: str, i: int) -> str | None:
    for op in _OPS:
        if source.startswith(op, i):
            return op
    return None


# ---------------------------------------------------------------------------
# Number mutation
# ---------------------------------------------------------------------------


def _mutate_number(text: str) -> str | None:
    """n → n+1, preserving base prefix and numeric suffix. None if unparseable."""
    prefix_len = 0
    base = 0
    low = text.lower()
    if low.startswith(("0x", "0b", "0o")):
        base = {"0x": 16, "0b": 2, "0o": 8}[low[:2]]
        prefix_len = 2
    if base:
        digits = "0123456789abcdefABCDEF" if base == 16 else ("01" if base == 2 else "01234567")
        j = len(text)
        while j > prefix_len and text[j - 1] not in digits:
            j -= 1
        suffix = text[j:]
        digits_part = text[:j]
    else:
        j = len(text)
        while j > 0 and text[j - 1].isalpha():
            j -= 1
        suffix = text[j:]
        digits_part = text[:j]
    core = digits_part.replace("_", "").replace("'", "")
    try:
        if any(ch in core for ch in ".eE"):
            val = float(core)
            new = f"{val + 1}{suffix}"
        elif base:
            val = int(core, base)
            new = _reemit_base(val + 1, base) + suffix
        else:
            val = int(core)
            new = f"{val + 1}{suffix}"
        return new
    except ValueError:
        return None


def _reemit_base(v: int, base: int) -> str:
    if base == 16:
        return f"0x{v:X}"
    if base == 8:
        return f"0o{v:o}"
    return f"0b{v:b}"


# ---------------------------------------------------------------------------
# Mutant generation
# ---------------------------------------------------------------------------


def _apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    if not edits:
        return source
    out: list[str] = []
    pos = 0
    for start, end, repl in sorted(edits):
        if start < pos:
            continue
        out.append(source[pos:start])
        out.append(repl)
        pos = end
    out.append(source[pos:])
    return "".join(out)


def _matching_paren(toks: list[_Tok], open_idx: int) -> int | None:
    depth = 0
    for k in range(open_idx, len(toks)):
        if toks[k].kind != "op":
            continue
        if toks[k].text == "(":
            depth += 1
        elif toks[k].text == ")":
            depth -= 1
            if depth == 0:
                return k
    return None


def _remove_stmt_edits(toks: list[_Tok], idx: int) -> list[tuple[int, int, str]] | None:
    """Compute the edit that deletes the single-line statement ending at toks[idx] (`;`)."""
    t = toks[idx]
    if t.depth != 0:
        return None
    start = None
    line_toks: list[_Tok] = []
    for k in range(idx, -1, -1):
        tk = toks[k]
        if tk.line == t.line:
            start = tk.start
            line_toks.append(tk)
        else:
            break
    line_toks.reverse()
    if len(line_toks) < 2:
        return None
    has_assign = any(tk.kind == "op" and tk.text == "=" for tk in line_toks)
    for tk in line_toks:
        if tk.kind == "directive":
            return None
        if tk.kind == "op" and tk.text in ("{", "}"):
            return None
        if tk.kind == "id" and tk.text in _BLOCKED_STMT:
            return None
    first = line_toks[0]
    if first.kind == "id" and first.text in _TYPE_KEYWORDS and not has_assign:
        return None
    return [(start, t.end, "")]


def _sites(t: _Tok, toks: list[_Tok], idx: int, source: str, lang: str) -> list[tuple[str, str, str, list[tuple[int, int, str]]]]:
    out: list[tuple[str, str, str, list[tuple[int, int, str]]]] = []
    if t.kind == "op":
        if t.text in _CMP_FLIP:
            out.append(("comparison", t.text, _CMP_FLIP[t.text], [(t.start, t.end, _CMP_FLIP[t.text])]))
        elif t.text in _BINOP_FLIP:
            if t.text == "*":
                prev = _prev_non_ws(toks, idx)
                if prev is None or (prev.kind == "id" and prev.text in _TYPE_KEYWORDS) or (
                    prev.kind not in ("id", "num", "str", "char") and prev.text not in (")", "]")
                ):
                    pass
                else:
                    out.append(("binop", t.text, _BINOP_FLIP[t.text], [(t.start, t.end, _BINOP_FLIP[t.text])]))
            else:
                out.append(("binop", t.text, _BINOP_FLIP[t.text], [(t.start, t.end, _BINOP_FLIP[t.text])]))
        elif t.text in _AUG_FLIP:
            out.append(("aug_assign", t.text, _AUG_FLIP[t.text], [(t.start, t.end, _AUG_FLIP[t.text])]))
        elif t.text in ("&&", "||"):
            repl = "||" if t.text == "&&" else "&&"
            out.append(("boolop", t.text, repl, [(t.start, t.end, repl)]))
        elif t.text == "!":
            out.append(("remove_not", t.text, "", [(t.start, t.end, "")]))
        elif t.text == ";":
            edits = _remove_stmt_edits(toks, idx)
            if edits:
                out.append(("remove_stmt", source[edits[0][0] : edits[0][1]], "", edits))
    elif t.kind == "id":
        if t.text in ("true", "false"):
            repl = "false" if t.text == "true" else "true"
            out.append(("bool_literal", t.text, repl, [(t.start, t.end, repl)]))
        elif t.text in ("if", "while"):
            nxt = _next_token(toks, idx)
            if nxt is not None and nxt[1].kind == "op" and nxt[1].text == "(":
                close = _matching_paren(toks, nxt[0])
                if close is not None:
                    cond = source[nxt[1].end : toks[close].start]
                    edits = [(nxt[1].end, nxt[1].end, "!("), (toks[close].start, toks[close].start, ")")]
                    out.append(("negate_condition", cond, f"!({cond})", edits))
    elif t.kind == "num":
        new = _mutate_number(t.text)
        if new and new != t.text:
            out.append(("num_literal", t.text, new, [(t.start, t.end, new)]))
    elif t.kind == "str" and not t.prefixed:
        new = '""' if len(t.text) > 2 else '"MUTANT"'
        out.append(("str_literal", t.text, new, [(t.start, t.end, new)]))
    return out


def _next_token(toks: list[_Tok], idx: int) -> tuple[int, _Tok] | None:
    for k in range(idx + 1, len(toks)):
        if toks[k].kind not in ("ws", "comment"):
            return k, toks[k]
    return None


def generate_cfamily_mutants(
    source: str,
    path: Path,
    lang: str,
    operators: list[str] | None = None,
) -> list[Mutant]:
    """One Mutant per (site, operator) producing a distinct file variant."""
    toks = [t for t in _tokenize(source, lang) if t.kind not in ("ws", "comment")]
    if not toks:
        return []
    ops: set[str] | None = set(operators) if operators else None

    mutants: dict[str, Mutant] = {}
    for i, t in enumerate(toks):
        for op_name, before, after, edits in _sites(t, toks, i, source, lang):
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


def _is_test_file(rel: Path, lang: str) -> bool:
    parts = rel.parts
    if any(part.lower() in _TEST_PARTS for part in parts[:-1]):
        return True
    stem = rel.stem.lower()
    if lang == "java":
        return stem.startswith("test") or stem.endswith("test") or stem.endswith("tests") or stem.endswith("testcase")
    if lang == "csharp":
        return stem.startswith("test") or stem.endswith("test") or stem.endswith("tests")
    return stem.startswith("test") or stem.endswith("test") or "_test" in stem or stem.startswith("test_")


def _iter_source_files(root: Path, lang: str):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part in _NOISE for part in rel.parts):
            continue
        if p.suffix not in SOURCE_EXTS[lang]:
            continue
        if _is_test_file(rel, lang):
            continue
        yield p


def collect_files(root: Path, lang: str, files: list[str] | None = None) -> list[Path]:
    """Source files to mutate (relative to root), optionally filtered by --files."""
    if files:
        wanted = {Path(f).as_posix() for f in files}
        out = []
        for p in sorted(_iter_source_files(root, lang)):
            rel = p.relative_to(root)
            if rel.as_posix() in wanted or str(rel) in wanted:
                out.append(p)
        return out
    return sorted(_iter_source_files(root, lang))


def collect_test_files(root: Path, lang: str) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in SOURCE_EXTS[lang]
        and not any(part in _NOISE for part in p.relative_to(root).parts)
        and _is_test_file(p.relative_to(root), lang)
    )


def _bounded_hit(root: Path, globs: list[str]) -> bool:
    for pat in globs:
        if next(root.glob(pat), None) is not None:
            return True
    return False


def detect_java(root: Path) -> bool:
    for name in ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        if (root / name).exists():
            return True
    return _bounded_hit(root, ["src/**/*.java", "**/*.java"])


def detect_csharp(root: Path) -> bool:
    return _bounded_hit(root, ["*.sln", "*.csproj", "**/*.csproj", "src/**/*.cs", "**/*.cs"])


def detect_cpp(root: Path) -> bool:
    for name in ("CMakeLists.txt", "Makefile", "meson.build", "configure.ac", "conanfile.txt"):
        if (root / name).exists():
            return True
    return _bounded_hit(root, ["src/**/*.cpp", "src/**/*.cc", "src/**/*.cxx", "src/**/*.h", "src/**/*.hpp", "**/*.cpp", "**/*.hpp"])


def default_test_command(root: Path, lang: str) -> str:
    if lang == "java":
        if (root / "pom.xml").exists():
            return "mvn -q test"
        if (root / "gradlew.bat").exists() or (root / "gradlew").exists():
            return "gradlew test"
        if _bounded_hit(root, ["build.gradle*", "build.gradle.kts"]):
            return "gradle test"
        return "mvn -q test"
    if lang == "csharp":
        return "dotnet test"
    if (root / "CMakeLists.txt").exists():
        return "ctest --output-on-failure"
    if (root / "Makefile").exists():
        return "make test"
    return "ctest --output-on-failure"


# ---------------------------------------------------------------------------
# verify (per-test-file contribution)
# ---------------------------------------------------------------------------


def _per_file_command(root: Path, lang: str, test_rel: Path, base: list[str]) -> list[str] | None:
    """Command that runs only the tests in `test_rel`; None if we can't build one."""
    stem = test_rel.stem
    if lang == "java":
        if "mvn" in base:
            return base + ["-Dtest=" + stem, "-DfailIfNoTests=false"]
        if any("gradle" in b or "gradlew" in b for b in base):
            return base + ["--tests", stem]
        return None
    if lang == "csharp":
        joined = " ".join(base)
        if "dotnet" in joined and "test" in joined.split():
            return base + ["--filter", f"FullyQualifiedName~{stem}"]
        return None
    return None


def verify_cfamily_project(root: Path, test_file: Path, lang: str, cfg) -> VerifyResult:
    """Per-test-file contribution: run the suite restricted to that test file.

    C-family targets have no per-line coverage in v1, so reach = all mutants.
    When no per-file invocation can be built (e.g. a custom test_command),
    the full suite is run and the result is suite-wide — a warning is printed.
    """
    from .runner import Runner

    test_file = Path(test_file)
    if not test_file.is_absolute():
        test_file = root / test_file
    test_rel = test_file.relative_to(root)

    base = shlex.split(cfg.test_command)
    cmd = _per_file_command(root, lang, test_rel, base)
    if cmd is None:
        cmd = base
        print(
            f"⚠️  No per-file test invocation for {lang} + '{cfg.test_command}'; "
            "verifying against the FULL suite (suite-wide contribution, not per-file).",
            file=sys.stderr,
        )

    mutants: list[Mutant] = []
    for f in collect_files(root, lang):
        rel = f.relative_to(root)
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        mutants.extend(generate_cfamily_mutants(source, rel, lang, operators=cfg.operators))

    cache_file = root / cfg.cache_file if cfg.cache else None
    runner = Runner(
        root,
        test_command=" ".join(cmd),
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
