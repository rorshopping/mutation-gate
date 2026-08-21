"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import collect_python_files, collect_test_files, load_config
from .coverage import collect_per_file_coverage, coverage_available, covered_lines_for_suite
from .diff import git_changed_lines
from .gate import should_pass
from .generate import generate_mutants
from .github import detect_pr, post_comment, render_pr_comment
from .model import MutantResult, Report
from .report import (
    html_report,
    junit_report,
    render_json,
    render_mutants,
    render_report,
    render_verify,
    report_to_dict,
    verify_to_dict,
)
from .runner import Runner, detect_project_python, filter_invalid
from .verify import verify_project


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _score(raw: str) -> float:
    try:
        v = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}")
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1, got {v}")
    return v


def _detect_language(root: Path, cfg) -> str:
    """Return the active language ('python', 'js', 'java', 'csharp', 'cpp', 'swift').

    Config wins; otherwise auto-detect with a js > csharp > java > cpp > swift > python priority.
    """
    from . import cfamily as cf

    lang = (cfg.language or "auto").strip().lower()
    if lang != "auto":
        if lang == "c++":
            lang = "cpp"
        if lang in ("python", "js") or lang in cf.LANGUAGES:
            return lang
        from . import swift as sw_mod

        if lang in sw_mod.LANGUAGES:
            return lang
    from . import js as js_mod

    if js_mod.detect_js(root, "auto"):
        if js_mod.js_available(root):
            return "js"
        if not collect_python_files(root, cfg):
            print(
                "⚠️  JS/TS project detected but @babel packages not found — "
                "run `npm install -D @babel/parser @babel/traverse @babel/generator @babel/types`.",
                file=sys.stderr,
            )
            return "js"
    if cf.detect_csharp(root):
        return "csharp"
    if cf.detect_java(root):
        return "java"
    if cf.detect_cpp(root):
        return "cpp"
    from . import swift as sw_mod

    if sw_mod.detect_swift(root):
        return "swift"
    return "python"


def _load_mutants(root: Path, cfg, args, language: str) -> list:
    """Collect source files, apply --files/--diff/coverage filters, generate mutants."""
    if language == "js":
        return _load_mutants_js(root, cfg, args)
    if language == "swift":
        return _load_mutants_swift(root, cfg, args)
    from . import cfamily as cf

    if language in cf.LANGUAGES:
        return _load_mutants_cf(root, cfg, args, language)

    files = collect_python_files(root, cfg)
    ops = cfg.effective_operators()

    if args.files:
        wanted = {Path(f) for f in args.files}
        keep = []
        for f in files:
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            if rel in wanted or f in wanted or Path(f) in wanted:
                keep.append(f)
        files = keep

    changed_lines: dict[Path, set[int]] | None = None
    if getattr(args, "diff", False):
        changed_lines = git_changed_lines(root)
        if changed_lines is None:
            print("⚠️  --diff requested but this is not a git repo — running full suite.", file=sys.stderr)
        else:
            keep = [f for f in files if _rel(root, f) in changed_lines]
            if not keep:
                print("No modified files to mutate — nothing to gate.", file=sys.stderr)
                return []
            files = keep

    covered: dict[Path, set[int]] | None = None
    project_python = detect_project_python(root)
    if getattr(args, "coverage_guided", False) or cfg.coverage_guided:
        if not coverage_available(project_python):
            print("⚠️  coverage.py not installed for the test interpreter — coverage-guided mode disabled. Install with `pip install coverage`.", file=sys.stderr)
        else:
            covered = covered_lines_for_suite(root, timeout=max(cfg.timeout, 300), project_python=project_python)
            if not covered:
                print("⚠️  Coverage run returned no data — running without line filtering.", file=sys.stderr)

    mutants = []
    for src in files:
        rel = _rel(root, src)
        if changed_lines is not None and rel in changed_lines:
            line_filter = changed_lines[rel]
        else:
            line_filter = None
        try:
            source = src.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in generate_mutants(source, rel, operators=ops, mutate_docstrings=cfg.mutate_docstrings):
            if changed_lines is not None and line_filter is not None and m.lineno not in line_filter:
                continue
            if covered is not None:
                lines = covered.get(m.file, set())
                if m.lineno not in lines:
                    continue
            mutants.append(m)
    return mutants


def _load_mutants_js(root: Path, cfg, args) -> list:
    from . import js as js_mod

    ops = cfg.operators
    files = js_mod.collect_js_files(root, args.files)

    changed_lines: dict[Path, set[int]] | None = None
    if getattr(args, "diff", False):
        changed_lines = git_changed_lines(root)
        if changed_lines is None:
            print("⚠️  --diff requested but this is not a git repo — running full suite.", file=sys.stderr)
        else:
            files = [f for f in files if _rel(root, f) in changed_lines]
            if not files:
                print("No modified files to mutate — nothing to gate.", file=sys.stderr)
                return []

    covered: dict[Path, set[int]] | None = None
    if getattr(args, "coverage_guided", False) or cfg.coverage_guided:
        if not js_mod.node_available():
            print("⚠️  Node not available — coverage-guided mode disabled.", file=sys.stderr)
        else:
            covered = js_mod.js_covered_lines_for_suite(root, timeout=max(cfg.timeout, 300))
            if not covered:
                print("⚠️  Coverage run returned no data — running without line filtering.", file=sys.stderr)

    mutants = []
    engine_bin = js_mod.js_runtime_binary(getattr(cfg, "js_runtime", "node"))
    rels = [f.relative_to(root) for f in files]
    try:
        batch = js_mod.generate_js_mutants_batch(root, rels, binary=engine_bin, operators=ops)
    except RuntimeError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        batch = None
    for f in files:
        rel = f.relative_to(root)
        file_mutants = batch.get(rel) if batch is not None else None
        if file_mutants is None:
            try:
                file_mutants = js_mod.generate_js_mutants(root, rel, operators=ops, binary=engine_bin)
            except RuntimeError as exc:
                print(f"⚠️  {exc}", file=sys.stderr)
                continue
        for m in file_mutants:
            if changed_lines is not None:
                lines = changed_lines.get(rel, set())
                if m.lineno not in lines:
                    continue
            if covered is not None:
                lines = covered.get(m.file, set())
                if m.lineno not in lines:
                    continue
            mutants.append(m)
    return mutants


def _load_mutants_cf(root: Path, cfg, args, language: str) -> list:
    """Collect Java/C#/C++ source files, apply --files/--diff filters, generate mutants."""
    from . import cfamily as cf

    files = cf.collect_files(root, language, args.files)

    changed_lines: dict[Path, set[int]] | None = None
    if getattr(args, "diff", False):
        changed_lines = git_changed_lines(root)
        if changed_lines is None:
            print("⚠️  --diff requested but this is not a git repo — running full suite.", file=sys.stderr)
        else:
            files = [f for f in files if _rel(root, f) in changed_lines]
            if not files:
                print("No modified files to mutate — nothing to gate.", file=sys.stderr)
                return []

    if getattr(args, "coverage_guided", False) or cfg.coverage_guided:
        print(
            f"⚠️  coverage-guided mode is not supported for {language} — running without line filtering.",
            file=sys.stderr,
        )

    mutants = []
    for f in files:
        rel = _rel(root, f)
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in cf.generate_cfamily_mutants(source, rel, language, operators=cfg.operators):
            if changed_lines is not None:
                lines = changed_lines.get(rel, set())
                if m.lineno not in lines:
                    continue
            mutants.append(m)
    return mutants


def _load_mutants_swift(root: Path, cfg, args) -> list:
    """Collect Swift source files, apply --files/--diff filters, generate mutants."""
    from . import swift as sw

    files = sw.collect_files(root, "swift", getattr(args, "files", None))

    changed_lines: dict[Path, set[int]] | None = None
    if getattr(args, "diff", False):
        changed_lines = git_changed_lines(root)
        if changed_lines is None:
            print("⚠️  --diff requested but this is not a git repo — running full suite.", file=sys.stderr)
        else:
            files = [f for f in files if _rel(root, f) in changed_lines]
            if not files:
                print("No modified files to mutate — nothing to gate.", file=sys.stderr)
                return []

    if getattr(args, "coverage_guided", False) or cfg.coverage_guided:
        print(
            "⚠️  coverage-guided mode is not supported for swift — running without line filtering.",
            file=sys.stderr,
        )

    mutants = []
    for f in files:
        rel = _rel(root, f)
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in sw.generate_swift_mutants(source, rel, operators=cfg.operators):
            if changed_lines is not None:
                lines = changed_lines.get(rel, set())
                if m.lineno not in lines:
                    continue
            mutants.append(m)
    return mutants


def _rel(root: Path, p: Path) -> Path:
    try:
        return p.relative_to(root)
    except ValueError:
        return p


def _apply_operator_filter(args, cfg) -> None:
    if getattr(args, "operators", None):
        from .operators import OPERATORS

        known = set(OPERATORS)
        requested = {o.strip() for o in args.operators.split(",") if o.strip()}
        unknown = requested - known
        if unknown:
            print(f"⚠️  Unknown operators (ignored): {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"    Available: {', '.join(sorted(known))}", file=sys.stderr)
        cfg.operators = sorted(requested & known)


def _build_subsets(source_to_tests: dict, mutants: list) -> tuple[dict[int, list[str]], int]:
    """Map each mutant index → sorted list of test files that cover its source line."""
    subsets: dict[int, list[str]] = {}
    reduced = 0
    for i, m in enumerate(mutants):
        covering = sorted(source_to_tests.get(m.file, set()))
        if covering:
            subsets[i] = [tf.as_posix() for tf in covering]
            reduced += 1
    return subsets, reduced


def _is_py_test_file(rel: Path) -> bool:
    parts = rel.parts
    if len(parts) > 1 and any(p in ("test", "tests") for p in parts[:-1]):
        return True
    stem = rel.stem
    return stem.startswith("test_") or stem.endswith("_test")


def _verify_changed_tests(root: Path, cfg, args, language: str, gate: float) -> bool:
    """Verify every test file changed vs HEAD; False if any is below `gate`.

    This is the AI-test wedge in PR CI: a PR that adds a theater test (high
    reach, low kill) fails the run even if the mutation score passes.
    """
    from .diff import git_changed_test_files

    changed = git_changed_test_files(root)
    if changed is None:
        print("⚠️  --verify-changed-tests requested but this is not a git repo.", file=sys.stderr)
        return True

    test_files: list[Path] = []
    from . import cfamily as cf

    for name in changed:
        rel = Path(name)
        if language == "python":
            if rel.suffix == ".py" and _is_py_test_file(rel) and (root / rel).exists():
                test_files.append(rel)
        elif language == "swift":
            from . import swift as sw_mod

            if (
                rel.suffix in sw_mod.SOURCE_EXTS["swift"]
                and sw_mod._is_test_file(rel)
                and (root / rel).exists()
            ):
                test_files.append(rel)
        elif language in cf.LANGUAGES:
            if rel.suffix in cf.SOURCE_EXTS[language] and cf._is_test_file(rel, language) and (root / rel).exists():
                test_files.append(rel)
        else:
            from . import js as js_mod

            if rel.suffix in js_mod._JS_EXTS and js_mod._is_test_file(rel) and (root / rel).exists():
                test_files.append(rel)

    if not test_files:
        print("No changed test files to verify — nothing to check.")
        return True

    print(f"\nVerifying {len(test_files)} changed test file(s) against a {gate * 100:.0f}% contribution gate…")
    all_ok = True
    for tf in sorted(test_files):
        if language == "python":
            vr = verify_project(root, tf, cfg)
        elif language == "swift":
            from . import swift as sw_mod

            vr = sw_mod.verify_swift_project(root, tf, "swift", cfg)
        elif language in cf.LANGUAGES:
            vr = cf.verify_cfamily_project(root, tf, language, cfg)
        else:
            from . import js as js_mod

            vr = js_mod.verify_js_project(root, tf, cfg)
        contrib = vr.contribution if vr.contribution is not None else 0.0
        ok = contrib >= gate
        all_ok = all_ok and ok
        verdict = "Strong" if contrib >= 0.7 else ("Decent" if contrib >= 0.5 else "Weak — likely a theater test")
        print(f"  {tf.as_posix()}: Contribution {contrib * 100:.1f}%  [{verdict}]  {'PASS' if ok else 'BELOW GATE'}")
    return all_ok


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root, args.config)
    cfg.workers = args.workers or cfg.workers
    cfg.timeout = args.timeout or cfg.timeout
    _apply_operator_filter(args, cfg)
    if args.no_cache:
        cfg.cache = False

    language = _detect_language(root, cfg)
    if language == "js" and cfg.test_command == "pytest":
        # Our own fallback default (never a user-authored command): pick the
        # runner matching js_runtime so "bun" actually speeds up per-mutant runs.
        cfg.test_command = "bun test" if getattr(cfg, "js_runtime", "node") == "bun" else "npm test"
    from . import cfamily as cf

    if language in cf.LANGUAGES and cfg.test_command in ("pytest", "pytest -q"):
        cfg.test_command = cf.default_test_command(root, language)
    if language == "swift" and cfg.test_command == "pytest":
        from . import swift as sw_mod

        cfg.test_command = sw_mod.default_test_command(root, "swift")

    mutants = _load_mutants(root, cfg, args, language)
    subsets: dict[int, list[str]] | None = None
    if getattr(args, "test_subset", False) or cfg.test_subset:
        if language == "python":
            project_python = detect_project_python(root)
            if not coverage_available(project_python):
                print("⚠️  coverage.py not installed for the test interpreter — --test-subset disabled. Install with `pip install coverage`.", file=sys.stderr)
            else:
                test_files = collect_test_files(root, cfg)
                if not test_files:
                    print("⚠️  No test files found — running full suite per mutant.", file=sys.stderr)
                else:
                    print(f"Building per-file test attribution across {len(test_files)} test files…", file=sys.stderr)
                    source_to_tests = collect_per_file_coverage(
                        root, test_files, timeout=max(cfg.timeout, 300), workers=cfg.workers, project_python=project_python
                    )
                    subsets, reduced = _build_subsets(source_to_tests, mutants)
                    print(f"Test subsetting: {reduced}/{len(mutants)} mutants will run a reduced test set.", file=sys.stderr)
        elif language == "js":
            from . import js as js_mod

            if not js_mod.node_available():
                print("⚠️  Node not available — --test-subset disabled.", file=sys.stderr)
            else:
                test_files = js_mod.collect_js_test_files(root)
                if not test_files:
                    print("⚠️  No test files found — running full suite per mutant.", file=sys.stderr)
                else:
                    print(f"Building per-file test attribution across {len(test_files)} test files…", file=sys.stderr)
                    source_to_tests = js_mod.collect_js_per_file_coverage(
                        root, test_files, timeout=max(cfg.timeout, 300), workers=cfg.workers
                    )
                    subsets, reduced = _build_subsets(source_to_tests, mutants)
                    print(f"Test subsetting: {reduced}/{len(mutants)} mutants will run a reduced test set.", file=sys.stderr)
        else:
            print(f"⚠️  --test-subset is not supported for {language} — running the full suite per mutant.", file=sys.stderr)

    cache_file = root / cfg.cache_file if cfg.cache else None
    runner = Runner(
        root,
        test_command=cfg.test_command,
        timeout=cfg.timeout,
        workers=cfg.workers,
        cache_file=cache_file,
        js_runtime=getattr(cfg, "js_runtime", "node"),
    )
    baseline_ok, baseline_out = runner.baseline()

    if not baseline_ok and not args.ignore_baseline:
        report = Report(results=[], baseline_failed=True, baseline_output=baseline_out, config_source=cfg.source)
        print(render_report(report))
        return 1

    valid, invalid = filter_invalid(mutants)
    if language != "python":
        valid, invalid = mutants, []

    if not valid and not invalid:
        print("No valid mutants generated from the selected files.")
        return 0

    def _progress(done, total):
        if args.verbose:
            print(f"  [{done}/{total}] mutants", file=sys.stderr)
        elif hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
            print(f"\r  [{done}/{total}] mutants ({done * 100 // total}%)", end="", file=sys.stderr)
            if done == total:
                print(file=sys.stderr)

    remote = getattr(args, "remote", None)
    if remote:
        results, cached = runner.run_distributed(
            remote, args.token or "", valid, progress=_progress, subsets=subsets
        )
    else:
        results, cached = runner.run(valid, progress=_progress, subsets=subsets)
    for m in invalid:
        results.append(MutantResult(mutant=m, status="invalid"))

    report = Report(results=results, config_source=cfg.source, cached=cached)
    print(render_report(report))
    if args.json:
        Path(args.json).write_text(render_json(report_to_dict(report)), encoding="utf-8")
        print(f"JSON written to {args.json}")
    if args.junit:
        Path(args.junit).write_text(junit_report(report), encoding="utf-8")
        print(f"JUnit XML written to {args.junit}")
    if args.html:
        Path(args.html).write_text(html_report(report, args.min_score), encoding="utf-8")
        print(f"HTML report written to {args.html}")

    exit_code = 0
    if args.min_score is not None:
        ok = should_pass(report, args.min_score)
        print(f"\nGate (min {args.min_score * 100:.0f}%): {'PASS' if ok else 'FAIL'}")
        if not ok:
            exit_code = 1

    changed_gate = getattr(args, "verify_changed_tests", None)
    if changed_gate is not None:
        if not _verify_changed_tests(root, cfg, args, language, changed_gate):
            exit_code = 1

    _maybe_post_comment(args, report)
    return exit_code


def _maybe_post_comment(args: argparse.Namespace, report) -> None:
    """Post a PR comment when running in GitHub Actions and a token exists."""
    if not getattr(args, "github_comment", False):
        return
    body = render_pr_comment(report, args.min_score)
    import os

    pr = detect_pr()
    token = os.environ.get("GITHUB_TOKEN")
    if not pr or not token:
        print("\n--github-comment set, but not in a GitHub Actions PR context with GITHUB_TOKEN.\nMarkdown preview:\n")
        print(body)
        return
    repo, number = pr
    ok = post_comment(repo, number, token, body)
    print(f"\nPR comment {'posted' if ok else 'FAILED'} to {repo}#{number}")


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root, args.config)
    cfg.workers = args.workers or cfg.workers
    cfg.timeout = args.timeout or cfg.timeout
    _apply_operator_filter(args, cfg)
    if args.no_cache:
        cfg.cache = False

    language = _detect_language(root, cfg)
    from . import cfamily as cf

    if language in cf.LANGUAGES and cfg.test_command in ("pytest", "pytest -q"):
        cfg.test_command = cf.default_test_command(root, language)
    if language == "swift" and cfg.test_command == "pytest":
        from . import swift as sw_mod

        cfg.test_command = sw_mod.default_test_command(root, "swift")

    if language == "js":
        from . import js as js_mod

        if not js_mod.js_available(root):
            print("⚠️  JS target needs Node + @babel packages installed in the project.", file=sys.stderr)
            return 1
        vr = js_mod.verify_js_project(root, Path(args.test_file), cfg)
    elif language == "swift":
        from . import swift as sw_mod

        vr = sw_mod.verify_swift_project(root, Path(args.test_file), "swift", cfg)
    elif language in cf.LANGUAGES:
        vr = cf.verify_cfamily_project(root, Path(args.test_file), language, cfg)
    else:
        vr = verify_project(root, Path(args.test_file), cfg)
    print(render_verify(vr, gate_score=args.gate))
    if args.json:
        Path(args.json).write_text(render_json(verify_to_dict(vr)), encoding="utf-8")
        print(f"JSON written to {args.json}")
    if args.gate is not None:
        ok = vr.contribution is not None and vr.contribution >= args.gate
        return 0 if ok else 1
    return 0


def cmd_mutate(args: argparse.Namespace) -> int:
    """Generate and list (optionally dump) mutants without running the suite."""
    root = Path(args.root).resolve()
    cfg = load_config(root, args.config)
    _apply_operator_filter(args, cfg)

    language = _detect_language(root, cfg)
    mutants = _load_mutants(root, cfg, args, language)
    valid, invalid = filter_invalid(mutants)
    if language != "python":
        valid, invalid = mutants, []

    print(render_mutants(valid))
    if invalid:
        print(f"\n{len(invalid)} mutants were invalid (syntax errors) and skipped.")
    if valid:
        from collections import Counter

        counts = Counter(m.operator for m in valid)
        by_op = ", ".join(f"{name}={n}" for name, n in sorted(counts.items()))
        print(f"\nBy operator: {by_op}")

    if args.out:
        suffix = {"js": "js", "java": "java", "csharp": "cs", "cpp": "cpp", "swift": "swift"}.get(language, "py")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        manifest = []
        for i, m in enumerate(valid):
            name = f"mutant-{i:04d}-{m.file.stem}-{m.lineno}-{m.operator}.{suffix}"
            (out / name).write_text(m.source, encoding="utf-8")
            manifest.append({"file": name, "source": str(m.file), "line": m.lineno, "operator": m.operator})
        (out / "manifest.json").write_text(render_json(manifest), encoding="utf-8")
        print(f"\nWrote {len(valid)} mutant files + manifest.json to {out}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg_file = root / ".mutation-gate.toml"
    if cfg_file.exists():
        print(f"{cfg_file} already exists — leaving as-is.")
        return 0
    language = _detect_language(root, load_config(root, getattr(args, "config", None)))
    from . import cfamily as cf

    include_globs = {
        "python": ["**/*.py"],
        "js": ["**/*.{js,jsx,ts,tsx}"],
        "java": ["**/*.java"],
        "csharp": ["**/*.cs"],
        "cpp": ["**/*.{cpp,cc,cxx,c,h,hpp,hh,hxx}"],
        "swift": ["**/*.swift"],
    }.get(language, ["**/*.py"])
    exclude_globs = {
        "python": ["**/test_*.py", "**/*_test.py", "**/tests/**", "**/.git/**"],
        "js": ["**/*.test.*", "**/*.spec.*", "**/test/**", "**/tests/**", "**/__tests__/**", "**/node_modules/**"],
        "java": ["**/*Test.java", "**/*Tests.java", "**/src/test/**", "**/target/**", "**/.git/**"],
        "csharp": ["**/*Tests.cs", "**/*Test.cs", "**/Tests/**", "**/obj/**", "**/bin/**", "**/.git/**"],
        "cpp": ["**/test_*", "**/*_test.*", "**/tests/**", "**/build/**", "**/.git/**"],
        "swift": ["**/*Tests.swift", "**/*Test.swift", "**/Tests/**", "**/DerivedData/**", "**/.build/**", "**/.git/**"],
    }.get(language, ["**/test_*.py", "**/*_test.py", "**/tests/**", "**/.git/**"])
    if language in cf.LANGUAGES:
        test_command = cf.default_test_command(root, language)
    elif language == "swift":
        from . import swift as sw_mod

        test_command = sw_mod.default_test_command(root, "swift")
    elif language == "js":
        test_command = "npm test"
    else:
        test_command = "pytest -q"
    import json

    js_runtime_line = '\njs_runtime = "node"  # switch to "bun" for faster per-mutant test runs\n' if language == "js" else ""
    cfg_file.write_text(
        "# Mutation Gate configuration\n"
        f'test_command = "{test_command}"\n'
        "timeout = 60\n"
        "workers = 4\n"
        "cache = true\n"
        "coverage_guided = false\n"
        "mutate_docstrings = false\n"
        f'language = "{language}"\n'
        f"{js_runtime_line}"
        f"include_globs = {json.dumps(include_globs)}\n"
        f"exclude_globs = {json.dumps(exclude_globs)}\n",
        encoding="utf-8",
    )
    print(f"Wrote {cfg_file} ({language})")
    return 0


def cmd_server(args: argparse.Namespace) -> int:
    from .server import make_server

    try:
        server = make_server(args.port, secret=args.token)
    except OSError as exc:
        print(f"server: failed to bind 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address
    print(f"mutation-gate broker listening on http://127.0.0.1:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nserver: shutting down", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .distributed import run_worker_loop

    try:
        run_worker_loop(
            args.server,
            args.token,
            Path(args.dir),
            poll_interval=args.poll,
        )
    except KeyboardInterrupt:
        print("\nworker: shutting down", file=sys.stderr)
    return 0


def _add_common_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("root", nargs="?", default=".", help="project root (default: cwd)")
    p.add_argument("--config", default=None, help="path to .mutation-gate.toml")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--timeout", type=int, default=None)
    p.add_argument("--json", default=None, help="write machine-readable report to this path")
    p.add_argument("--no-cache", action="store_true", help="disable the on-disk result cache")
    p.add_argument("--operators", default=None, help="comma-separated operator names to run")
    p.add_argument("--files", nargs="*", default=None, help="only mutate these files (paths relative to root)")
    p.add_argument("--diff", action="store_true", help="only mutate lines changed vs HEAD (git delta mode)")
    p.add_argument("--coverage-guided", action="store_true", help="only mutate lines covered by the test suite")
    p.add_argument("--test-subset", action="store_true", help="run only covering tests per mutant (needs coverage)")
    p.add_argument("--github-comment", action="store_true", help="post a markdown PR comment when in GitHub Actions")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mutation-gate", description=__doc__)
    p.add_argument("--version", action="version", version=f"mutation-gate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run full mutation score over the project")
    _add_common_run_flags(r)
    r.add_argument("--min-score", type=_score, default=None, help="gate: fail if score below this")
    r.add_argument(
        "--verify-changed-tests",
        nargs="?",
        const=0.5,
        type=_score,
        default=None,
        help="verify test files changed vs HEAD; fail if any contribution is below this (default 0.5)",
    )
    r.add_argument("--junit", default=None, help="write JUnit XML to this path (CI)")
    r.add_argument("--html", default=None, help="write a self-contained HTML report to this path")
    r.add_argument("--ignore-baseline", action="store_true", help="mutate even if tests currently fail")
    r.add_argument("--remote", default=None, help="distributed broker URL (http://host:port); workers execute mutants")
    r.add_argument("--token", default=None, help="secret for the distributed broker (--remote)")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="Contribution of one test file (AI-test theater check)")
    v.add_argument("test_file", help="test file to evaluate")
    _add_common_run_flags(v)
    v.add_argument("--gate", type=_score, default=None, help="fail if contribution below this (0..1)")
    v.set_defaults(func=cmd_verify)

    m = sub.add_parser("mutate", help="Generate and list mutants without running the suite")
    _add_common_run_flags(m)
    m.add_argument("--out", default=None, help="directory to dump mutant source files")
    m.set_defaults(func=cmd_mutate)

    i = sub.add_parser("init", help="Write a starter .mutation-gate.toml")
    i.add_argument("root", nargs="?", default=".")
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("server", help="Run a distributed job broker (dev reference)")
    s.add_argument("--port", type=int, default=8732, help="listen port (default 8732)")
    s.add_argument("--token", default="", help="require this token from clients/workers")
    s.set_defaults(func=cmd_server)

    w = sub.add_parser("worker", help="Run a distributed worker against a local checkout")
    w.add_argument("--server", default="http://127.0.0.1:8732", help="broker URL (default http://127.0.0.1:8732)")
    w.add_argument("--token", default="", help="broker secret")
    w.add_argument("--dir", default=".", help="local checkout of the project (must match the client's)")
    w.add_argument("--poll", type=float, default=0.5, help="idle poll interval in seconds")
    w.set_defaults(func=cmd_worker)

    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
