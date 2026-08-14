# Mutation Gate

Mutation testing as the verification gate for AI-written tests.

AI agents write most new tests now — and often the *same model* wrote the code too, so the
tests are tautological: they pass even when the code is broken. Code coverage can't catch
that (a test that merely calls a function and asserts "not None" covers all its lines).
**Mutation score can**: it measures whether a test suite *fails on a deliberately broken
program*. If it doesn't, the tests are theater.

## Commands

```
mutation-gate init                         # write .mutation-gate.toml
mutation-gate run                          # full mutation score, exit 0/1
mutation-gate run --min-score 0.8          # CI gate: fail if score < 80%
mutation-gate verify tests/test_x.py       # does THIS test file actually prove anything?
mutation-gate mutate                       # list (or --out dump) mutants without running
mutation-gate run --diff                   # gate only lines changed vs HEAD (PR delta)
```

### Flags

| Flag | Applies to | Effect |
| --- | --- | --- |
| `--min-score 0.8` | run | exit 1 unless score ≥ 80% |
| `--coverage-guided` | run | only mutate lines the suite actually executes (needs `coverage`) |
| `--diff` | run | only mutate lines changed vs `HEAD` in git |
| `--files a.py b.py` | run/mutate | only mutate the given files |
| `--operators comparison,binop` | run/mutate/verify | restrict the operator set |
| `--junit file.xml` | run | write JUnit XML for CI ingestion |
| `--json file.json` | run/verify | write machine-readable results |
| `--no-cache` | run/verify | disable the on-disk result cache |
| `--workers N`, `--timeout N` | run/verify | tune parallelism / per-mutant timeout |
| `--ignore-baseline` | run | mutate even if the suite currently fails |
| `--gate 0.6` | verify | exit 1 unless this test's contribution ≥ 60% |

## Caching

Results are cached in `.mutation-gate/cache.json`, keyed by a fingerprint of the test
command and every Python file in the project. Re-running an unchanged project replays
results instantly (`Cache hits: N`); any source or test change invalidates the cache.

## Operators

12 operators: comparison flips (`< ↔ <=`, `== ↔ !=`, `in ↔ not in`, `is ↔ is not`),
boolean/logic flips (`and ↔ or`, `True ↔ False`, `not x → x`), numeric/string literal
mutations (docstrings skipped by default), binary & augmented-assign flips, `return → return`,
condition negation, `range()` bound decay, and statement removal.

## Demo

```
cd examples/demo
pip install pytest coverage
python -m pytest -q                                   # both suites pass — looks healthy
mutation-gate run --min-score 0.8                     # real tests score 91.5%
mutation-gate verify tests/test_theater.py            # theater test: contribution 28%
```

`verify` is the AI-test wedge: it runs the suite with **only** that test file against every
mutant the file's own coverage reaches. High reach / low kill = the test has no teeth.

## CI

Copy `examples/ci/mutation-gate.yml` into your repo's `.github/workflows/` (it uses the
reusable composite action in `.github/actions/mutation-gate/`, installs the tool, runs the
gate at 80%, and uploads the JUnit report as an artifact). Or call the action directly:

```yaml
- uses: ./.github/actions/mutation-gate
  with:
    min-score: 0.8
    args: --coverage-guided
```

## Layout

- `src/mutation_gate/` — engine (operators, generation, runner, cache, coverage, verify, gate)
- `examples/demo/` — dogfood project with real tests vs theater tests
- `examples/ci/` — GitHub Action workflow template
- `tests/` — the tool's own test suite (59 tests)

## Roadmap

- v0.2 ✅ shared worktree cache, on-disk result cache, coverage-guided `run`, delta mode
- v0.3: JS/TS target; hosted distributed runner (monetization); GitHub App / Action parity
