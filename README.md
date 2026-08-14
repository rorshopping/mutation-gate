# Mutation Gate

Mutation testing as the verification gate for AI-written tests.

AI agents write most new tests now — and often the *same model* wrote the code too, so the
tests are tautological: they pass even when the code is broken. Code coverage can't catch
that (a test that merely calls a function and asserts "not None" covers all its lines).
**Mutation score can**: it measures whether a test suite *fails on a deliberately broken
program*. If it doesn't, the tests are theater.

## Install

```
pip install mutation-gate        # from PyPI (once published) — or:
pip install -e ".[dev]"          # from this repo
```

Requires Python ≥ 3.11. For the JS/TS target you also need Node and, in the project,
`npm install -D @babel/parser @babel/traverse @babel/generator @babel/types`.

## Commands

```
mutation-gate init                         # write .mutation-gate.toml
mutation-gate run                          # full mutation score, exit 0/1
mutation-gate run --min-score 0.8          # CI gate: fail if score < 80%
mutation-gate verify tests/test_x.py       # does THIS test file actually prove anything?
mutation-gate mutate                       # list (or --out dump) mutants without running
mutation-gate run --diff                   # gate only lines changed vs HEAD (PR delta)
mutation-gate server --token SECRET        # run a distributed job broker
mutation-gate worker --token SECRET        # run a worker against your checkout
mutation-gate run --remote http://host:8732 --token SECRET   # execute mutants remotely
```

### Flags

| Flag | Applies to | Effect |
| --- | --- | --- |
| `--min-score 0.8` | run | exit 1 unless score ≥ 80% |
| `--verify-changed-tests 0.5` | run | verify every test file changed vs HEAD (incl. untracked); exit 1 if any contribution < 50% |
| `--coverage-guided` | run | only mutate lines the suite actually executes (Python: `coverage`; JS: Node's test coverage) |
| `--test-subset` | run | run only the tests that touch each mutated file (Python: `coverage`; JS: Node's test coverage) |
| `--diff` | run | only mutate lines changed vs `HEAD` in git |
| `--files a.py b.py` | run/mutate | only mutate the given files |
| `--operators comparison,binop` | run/mutate/verify | restrict the operator set |
| `--junit file.xml` | run | write JUnit XML for CI ingestion |
| `--html file.html` | run | write a self-contained HTML report |
| `--json file.json` | run/verify | write machine-readable results |
| `--no-cache` | run/verify | disable the on-disk result cache |
| `--github-comment` | run | post a markdown PR comment (needs `GITHUB_TOKEN` in Actions) |
| `--workers N`, `--timeout N` | run/verify | tune parallelism / per-mutant timeout |
| `--ignore-baseline` | run | mutate even if the suite currently fails |
| `--gate 0.6` | verify | exit 1 unless this test's contribution ≥ 60% |
| `--remote URL` | run | execute mutants via a distributed broker (`server` + `worker`) |
| `--token SECRET` | run | shared secret for the distributed broker |

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

## JS / TypeScript target

Mutation-gate also mutates JavaScript and TypeScript (`.js/.jsx/.ts/.tsx`) via a bundled
Babel engine, and runs the suite with `npm test` (or any command in `test_command`). Auto-
detected when a `package.json` + source files are present (override with `language =
"python" | "js"` in config). Test files (`*.test.js`, `*.spec.js`, `test/`, `__tests__/`) are
never mutated. Requires Node + Babel packages in the project:

```
npm install -D @babel/parser @babel/traverse @babel/generator @babel/types
cd examples/demo-js
mutation-gate run . --min-score 0.8        # real tests: 94.0%
mutation-gate verify test/math.theater.test.js   # theater test: 22.0%
```

Same 11 core operators as Python (comparison, binop, boolop, aug_assign, bool_literal,
num_literal, str_literal, remove_not, negate_condition, return_none, remove_stmt).
Coverage-guided and test-subset modes work too, using Node's built-in test coverage (LCOV
reporter) instead of `coverage.py` — e.g. `mutation-gate run . --test-subset` runs only the
covering test files per mutant, and `verify` filters to the lines the evaluated test actually
executes.

## Distributed runner

Mutation runs are embarrassingly parallel, so `run` can push the work to any number of
machines. A tiny stdlib HTTP broker (`server`) hands each mutant to the first free `worker`,
which executes it against its own checkout and reports back:

```
mutation-gate server --port 8732 --token SECRET        # machine A
mutation-gate worker --server http://B:8732 --token SECRET --dir /path/to/repo   # machine B, C, ...
mutation-gate run . --remote http://B:8732 --token SECRET
```

- The client serializes mutants into a job; workers pull tasks (`/v1/tasks/next`), run the
  test command against their **local** copy of the repo, and post results. Cache replay,
  `--min-score`, `--test-subset`, and report output all behave identically to a local run.
- Zero dependencies — the broker and worker are pure stdlib (`http.server`, `urllib`). This
  is the open-source core of the planned hosted add-on: point `--remote` at a cloud broker
  and workers clone the repo at a pinned commit instead of a `--dir` checkout.
- `server --port 0` picks an ephemeral port (handy for tests).

## CI

Copy `examples/ci/mutation-gate.yml` into your repo's `.github/workflows/` (it uses the
reusable composite action in `.github/actions/mutation-gate/`, installs the tool, runs the
gate at 80%, and uploads the JUnit report as an artifact). Or call the action directly:

```yaml
- uses: ./.github/actions/mutation-gate
  with:
    min-score: 0.8
    args: --coverage-guided
    github-token: ${{ secrets.GITHUB_TOKEN }}   # optional — posts the report as a PR comment
```

Give the workflow `permissions: pull-requests: write` and pass `github-token` to post the
mutation report as a comment on every PR. Add `--verify-changed-tests 0.5` to the same run to
also flag **theater tests added by the PR** — any changed test file whose contribution falls
below 50% fails the gate (the AI-test wedge: coverage alone can't catch it).

## Layout

- `src/mutation_gate/` — engine (operators, generation, runner, cache, coverage, verify, gate)
- `src/mutation_gate/server.py` — distributed broker (stdlib `http.server`)
- `src/mutation_gate/distributed.py` — distributed client + worker
- `src/mutation_gate/js/engine.mjs` — Babel-based JS/TS mutator (Node side)
- `examples/demo/` — Python dogfood project with real tests vs theater tests
- `examples/demo-js/` — JS/TS dogfood project (Node's built-in test runner)
- `examples/ci/` — GitHub Action workflow template
- `tests/` — the tool's own test suite (101 tests)

## Roadmap

- v0.2 ✅ shared worktree cache, on-disk result cache, coverage-guided `run`, delta mode
- v0.3 ✅ JS/TS target (Babel engine, npm test runner)
- v0.4 ✅ JS coverage-guided `run` + test subsetting (Node LCOV); `--verify-changed-tests`
  PR gate; more operators
- v0.5 ✅ distributed runner (`server` / `worker` / `run --remote`)
- v0.6 — hosted distributed runner (the opencode-go model), GitHub App / PR checks,
  JS/TS coverage parity on `verify`
