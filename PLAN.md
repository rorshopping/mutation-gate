# Mutation Gate — plan

## One-liner
A mutation-testing tool whose job is to catch **theater tests** — especially AI-written
ones — by measuring whether a test suite actually proves behavior, and gating CI on it.

## Why (market wedge)
- AI agents now write most new tests; the *same model* often writes the code and the tests,
  so tests are tautological (pass for the wrong reasons). Coverage can be gamed; mutation
  score cannot (a test must *fail on a broken program* to count).
- Best-in-class AI test generators hit only ~71% mutation score; real AI suites average
  ~58% vs 92% line coverage. Nobody owns test-*quality* verification. Qodo/Veriva review
  code; nobody reviews the reviews.
- Mutation testing is compute-heavy (suite runs × mutants) → natural open-core shape:
  OSS engine + hosted distributed runner (opencode-go model).

## Shipped (v0.1 → v0.6)

### CLI: `init | run | verify | mutate`
- `run` — full-project mutation score, parallel across worker processes, text/JSON/JUnit/HTML
  reports, `--min-score` CI gate (0 pass / 1 fail).
- `verify <testfile>` — per-test-file contribution (coverage-guided). Low kill/reach = theater.
- `mutate` — generate/list mutants without running; `--out` dumps sources + manifest.
- `init` — scaffold `.mutation-gate.toml`.
- `--test-subset` — per-mutant test attribution: run only the tests that cover the mutated
  file (identical score, much faster).
- `--github-comment` — in GitHub Actions, post the mutation report as a PR comment
  (markdown table of survivors + gate verdict).

### Engine
- 12 AST operators (stdlib `ast`, `ast.unparse`): comparison flips incl. `in ↔ not in` and
  `is ↔ is not`; `and ↔ or`; `True ↔ False`; `not x → x`; numeric/string literal
  (docstrings skipped by default); binary (`+↔-`, `*↔//`, `%↔//`, `|↔&`) and augmented-assign
  flips; condition negation; `return X → return`; `range()` bound decay; statement removal.
- Per-mutant dedup by sha1 of mutated source; invalid mutants (won't compile) excluded.
- **Persistent per-worker worktrees** — the project is copied once per worker process,
  not once per mutant (was O(mutants) copies; now O(workers)).
- **On-disk result cache** (`.mutation-gate/cache.json`): keyed by a fingerprint of the test
  command + every `.py` file in the project. Unchanged re-runs replay instantly.
- **Coverage-guided `run`** (`--coverage-guided`): only mutate lines the suite executes.
- **Per-mutant test subsetting** (`--test-subset`): coverage attribution maps each source
  file to the tests that touch it; each mutant runs only those tests (identical score).
- **Delta mode** (`--diff`): git-aware — only mutate lines changed vs `HEAD` (Python + JS).
- **Changed-test gate** (`--verify-changed-tests 0.5`): on `run`, every test file changed vs
  HEAD (including untracked) is verified; any contribution below the gate fails the run. This
  is the PR-CI wedge against theater tests.
- **JS/TS target**: bundled Babel engine (`js/engine.mjs`) mutates `.js/.jsx/.ts/.tsx`
  (11 operators incl. `aug_assign`), runs via `npm test` (auto-detected with `package.json`);
  test files never mutated; `verify` with line-level filtering; coverage-guided `run` and
  per-mutant test subsetting via Node's built-in test coverage (LCOV reporter).
- **Java / C# / C++ targets**: bundled pure-Python tokenizer engine (`cfamily.py`) mutates
  `.java`, `.cs`, `.cpp/.cc/.cxx/.c/.h/...` with zero per-language dependencies; in-place token
  edits preserve formatting/comments byte-for-byte. 10 operators; suite runs via the project's
  build tool (Maven/Gradle, `dotnet test`, CMake/ctest or Make — auto-detected, overridable);
  test files never mutated; `verify` filters per test file (`-Dtest`, `--tests`,
  `dotnet test --filter`; full suite otherwise). Language auto-detect + language-aware `init`.
  C-family compile-failure mutants count as killed (no syntax filter); C/C++ pointer-decl
  and C#/C++ prefixed-string false sites guarded.
- **Distributed runner** (`server` / `worker` / `run --remote`): a stdlib HTTP broker hands
  each mutant to the first free worker, which runs it against its own checkout. The client
  polls until the job completes and reuses the same cache / reports / gates as a local run.
  Pure stdlib (`http.server`, `urllib`), zero dependencies — the OSS core of the hosted
  add-on (the opencode-go model).
- Config precedence: CLI > `.mutation-gate.toml` > `[tool.mutation-gate]` > defaults, with
  `--operators`, `--files`, `--workers`, `--timeout`, `--no-cache` overrides.

### Tests
101 tests → 161: operators, generation, config, scoring/gate, cache, diff parsing,
JUnit/JSON/HTML reports, PR-comment rendering, JS engine + JS coverage/subset + JS verify,
changed-test gate, full pipeline + theater detection, distributed broker + worker +
remote-vs-local parity; C-family tokenizer units, detection, default commands, CLI
integration (incl. C# demo end-to-end on the `dotnet` SDK).

### Demo / CI
- `examples/demo` — Python: real tests 91.5%; theater test ~28% and gated out.
- `examples/demo-js` — JS/TS: real tests 94.0%; theater test 22.0%.
- `examples/demo-java` / `examples/demo-csharp` / `examples/demo-cpp` — C-family mirrors of
  the demo (`Calculator`/`math`): ~90% real-test scores; all dogfooded in CI (Maven, `dotnet`,
  CMake on the GitHub-hosted runners).
- `examples/ci/mutation-gate.yml` — GitHub Action: install, gate at 80%, upload JUnit,
  optional PR comment via `github-token`.
- Distributed demo: `server` + `worker` on any machines, then `run --remote` (verified
  end-to-end: 91.5% remote == 91.5% local on the demo project).

## Roadmap
- v0.7: hosted distributed runner (the monetization); GitHub App; coverage-guided `run` and
  `--test-subset` for the C-family targets; more operators (boundary/number-range,
  function-call removal).
- Known limits: Python `ast.unparse` reformats files (comments/encoding dropped) — acceptable
  trade-off for one-mutant-per-file correctness.
