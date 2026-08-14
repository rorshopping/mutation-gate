"""verify: contribution of a single test file to mutation killing."""

from __future__ import annotations

from pathlib import Path

from .config import Config, collect_python_files, load_config
from .coverage import covered_lines_for_test
from .generate import generate_mutants
from .model import Mutant, MutantResult, VerifyResult
from .runner import Runner, filter_invalid


def verify_project(root: Path, test_file: Path, cfg: Config | None = None) -> VerifyResult:
    cfg = cfg or load_config(root)
    test_file = Path(test_file)
    if not test_file.is_absolute():
        test_file = root / test_file

    covered = covered_lines_for_test(root, test_file, timeout=max(cfg.timeout, 300))
    ops = cfg.effective_operators()

    # Build mutants only for source files the test file touches.
    mutants: list[Mutant] = []
    files = collect_python_files(root, cfg)
    for src in files:
        rel = src.relative_to(root)
        if covered and rel not in covered:
            continue
        try:
            source = src.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in generate_mutants(source, rel, operators=ops, mutate_docstrings=cfg.mutate_docstrings):
            m.original = source
            mutants.append(m)

    # Coverage filtering: keep only mutants whose line the test actually executes.
    if covered:
        filtered: list[Mutant] = []
        for m in mutants:
            lines = covered.get(m.file, set())
            if m.lineno in lines:
                filtered.append(m)
        mutants = filtered
        coverage_ok = True
    else:
        coverage_ok = False

    valid, invalid = filter_invalid(mutants)

    # Run each valid mutant with ONLY the target test file.
    test_rel = test_file.relative_to(root).as_posix()
    cfg.test_command = f"pytest {test_rel} -q"
    cache_file = root / cfg.cache_file if cfg.cache else None
    runner = Runner(
        root,
        test_command=cfg.test_command,
        timeout=cfg.timeout,
        workers=cfg.workers,
        cache_file=cache_file,
    )
    results, _cached = runner.run(valid)

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
        covered_files=covered,
        reachable=len(valid),
        killed=killed,
        survived=survived,
        invalid=len(invalid),
        survivors=survivors,
        coverage_available=coverage_ok,
    )
