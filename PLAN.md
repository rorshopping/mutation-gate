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

## Shipped (v0.1 + v0.2)

### CLI: `init | run | verify | mutate`
- `run` — full-project mutation score, parallel across worker processes, text/JSON/JUnit
  reports, `--min-score` CI gate (0 pass / 1 fail).
- `verify <testfile>` — per-test-file contribution (coverage-guided). Low kill/reach = theater.
- `mutate` — generate/list mutants without running; `--out` dumps sources + manifest.
- `init` — scaffold `.mutation-gate.toml`.

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
- **Delta mode** (`--diff`): git-aware — only mutate lines changed vs `HEAD`.
- Config precedence: CLI > `.mutation-gate.toml` > `[tool.mutation-gate]` > defaults, with
  `--operators`, `--files`, `--workers`, `--timeout`, `--no-cache` overrides.

### Tests
59 tests: operators, generation, config, scoring/gate, cache, diff parsing, JUnit/JSON
reports, full pipeline + theater-detection integration.

### Demo / CI
- `examples/demo` — real tests score 91.5%; theater test contributes ~28% and is gated out.
- `examples/ci/mutation-gate.yml` — GitHub Action: install, gate at 80%, upload JUnit.

## Roadmap
- v0.3: JS/TS target (jsdiff/Babel); hosted distributed runner (the monetization);
  GitHub Action as a reusable action + PR comments; `verify` gate wiring into PR checks.
- Known limits: `ast.unparse` reformats files (comments/encoding dropped) — acceptable
  trade-off for one-mutant-per-file correctness.
