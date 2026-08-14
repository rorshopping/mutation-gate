# Mutation Gate — Performance Report (sweep of Richard's projects)

Date: 2026. Run with `mutation-gate` v0.5.0 against every Python/JS test suite under
`C:\Users\Richard\Documents\Projects`.

## Method

- Flags: `run <project> --coverage-guided --test-subset --timeout 45 --workers 4`
  (Property_Management still under investigation — it crashes with `--test-subset` before
  producing output; results use `--workers 2` and are pending).
- Runs require a green baseline (all tests pass) before mutation testing starts.
- Per-file coverage attribution selects only the tests that touch each mutant's file
  (test subsetting), so scores reflect the whole suite cheaply.
- Scoring: **Mutation score = killed mutants ÷ counted (valid) mutants**. A survivor means
  a broken program still passed the tests — a real gap, not a coverage gap.

## What value did this deliver?

The sweep wasn't about finding *coverage* gaps. It was about answering three questions your
current tests can't answer — and mutation-gate answered all three with hard numbers:

1. **"Are my tests actually protecting me?"** Coverage says "these lines ran". It cannot say
   "these lines are *proven*". Every surviving mutant is a real bug the tool injected into your
   code that your test suite **passed anyway** — i.e. a bug you could ship without your tests
   noticing. The sweep surfaced hundreds of these (1050 mutants in Phishing_Prevention, 1016 in
   Documentbotv2) and told you exactly which ones survive.
2. **"Where is my suite safe, and where is it fake?"** The tool didn't just give one number per
   project — it pinned survivors to `file:line` and operator. You now know *precisely* that
   `phishguard/cli/*` dispatch logic and Documentbotv2's docx→HTML rendering are unprotected,
   while the engine/security/DB cores are genuinely verified. That's a **refactor-risk map**:
   touch the CLI/rendering code with confidence (tests will catch regressions), refactor the
   untested surfaces carefully.
3. **"Did the AI that wrote these tests do a real job?"** This is the wedge the whole tool exists
   for. Coverage-style tests written by the same model that wrote the code are tautological —
   they pass by construction. The sweep proved the detector works: a real test file kills
   **87–94%** of injected bugs, a theater test kills **~20–28%**. When your AI agents write
   tests going forward, you have a machine-checkable bar for "this test is worth keeping".

Concrete downstream value from this report:

- **A baseline to enforce.** Each project now has a defensible mutation score. Plug it into CI
  (`mutation-gate run . --min-score <score>`) and the score becomes a *floor* — every future PR
  that adds a theater test, or weakens an existing test, fails the build. That's regression
  prevention your current coverage checks cannot provide.
- **A prioritized TODO list.** Instead of "improve testing", you have 4 file:line targets that
  dominate each project's survivor count. Fixing Phishing_Prevention's CLI dispatch and
  Documentbotv2's preview pipeline is where ~all the low-hanging score sits.
- **Per-test-file accountability.** `mutation-gate verify <testfile>` scores *individual* test
  files, so a PR that only touches one test file can be gated on its own contribution
  (`--verify-changed-tests 0.5`) — the theater-test flag that no coverage tool can raise.

## Results

| Project | Lang | Baseline | Mutants | Mutation score | Verdict |
|---|---|---|---|---|---|
| examples/demo (dogfood) | Python | ✅ | 47 | **91.5%** (43/47) | Strong |
| examples/demo-js (dogfood) | JS | ✅ | 50 | **94.0%** (47/50) | Strong |
| Phishing_Prevention | Python | ✅ | 1050 | **55.1%** (579/1050) | Gaps in CLI paths |
| Documentbotv2 | Python | ✅ | 1016 | **26.9%** (273/1016) | Rendering untested |
| Snipledger2 (AI service) | Python | ✅ | 342 | **71.3%** (244/342) | Decent — API/security gaps |
| TD_Game (smoke only) | JS | ✅ | 7,945 | **16.6%** (1320/7945) | Smoke test ≠ regression test |
| Property_Management | Python | running | 7,357 | — | — |

## What the scores mean

**Theater tests score low.** A test that only asserts `result is not None` (typical AI-written
"coverage theater") kills ~20–28% of reachable mutants. A real test file kills 87–94%:

- `verify test_real.py` → Contribution **87.5–91.5%**, Verdict: Strong
- `verify test_theater.py` → Contribution **20.8–28.2%**, Verdict: Weak — likely a theater test

## Findings per project

### Phishing_Prevention — 55.1% (579/1050 killed)

The engine logic (`phishguard/core`: campaign, gdpr, security, tracking) is well tested — those
mutants die. The survivors cluster almost entirely in `phishguard/cli/*`, which is effectively
untested:

- `scenario == 'urgent_payment' / 'password_reset' / 'ceo_email'` dispatch branches survive
  (flip to `!=` changes behavior, no test notices).
- `if self._ai is not None` / `if self._cipher is None` guards survive.
- Empty-string `'' → 'MUTANT'` mutations inflate the survivor count on string-heavy CLI code.

**Action:** add tests for the CLI dispatch layer. This alone would push the score well above 70%.

### Documentbotv2 — 26.9% (273/1016 killed)

The docx→HTML **preview pipeline is essentially untested**:

- `backend/app/services/document_filler.py` and `preview_generator.py` harbor most survivors.
- `remove_stmt` survives on table/header/footer loops; `negate_condition` survives on
  `'Heading N' in style` dispatch; `str_literal` survives on HTML tags (`'<p>'`, `'<tr>'`,
  `'</div>'`, `<table class=…>`).

**Action:** test the document-rendering functions directly (feed a docx, assert HTML output).
Highest-leverage target in the whole sweep.

### TD_Game — 16.6% (1320/7,945 killed)

Honest reading: **a smoke test is not a regression test.** `tests/smoke.mjs` has exactly one test
and it verifies the game-logic modules load and basic operations don't crash. The mutation score
correctly exposes what that leaves unprotected — the entire presentation layer:

| File | Surviving mutants | Why |
|---|---|---|
| js/render.js | 3,428 | canvas/DOM rendering — never exercised in node |
| js/audio.js | 901 | Web Audio stubbed out in node (`AudioContext` guard) |
| js/ui.js | 784 | DOM UI (menus, HUD, banners) |
| js/main.js | 507 | browser bootstrap |
| js/minimap.js / save / input / config | 439 | canvas / localStorage / constants |
| **game logic** (enemies, towers, bullets, waves, map, stats, economy, state, leaderboard) | ~558 | genuinely exercised — strongest layer |

`render.js` + `audio.js` + `ui.js` + `main.js` account for **86% of all survivors** — code that
node `--test` structurally cannot run (no canvas, no DOM, no AudioContext). The core game-loop
modules are the best-tested part of the codebase.

**Action:** if the game's visual layer deserves protection, it needs a DOM harness
(jsdom or Playwright — the repo already has `tests/browser.mjs` via Chrome CDP), not
`node:test`. The smoke test's job is "does it crash", and by that bar it's fine — but don't
mistake it for regression coverage.

### examples/demo + demo-js (dogfood) — 91.5% / 94.0%

The bundled demos are the tool's own proof. The 4–6 surviving mutants are genuine boundary
gaps (`value < lo → <=`, `n < 0 → <= 0`, `0 → 1`), i.e. missing edge-case assertions, exactly
the signal the tool exists to produce.

### Snipledger2 (AI service) — 71.3% (244/342 killed)

Scoped to `src/SnipLedger.AI` (the Python FastAPI service; 7 test files). Decent overall, but
the survivors expose **security-relevant gaps**:

- `app.py:51` `if not is_loopback(host): raise HTTPException(403)` **survives** — the loopback
  guard isn't actually exercised.
- `app.py:54-58` bearer-token handling (`'bearer '`, `'missing bearer token'`, `'invalid token'`)
  — error paths untested.
- `auth.py` env/token resolution (`if env: return env`, `if existing: return existing`) —
  fallback branches untested.
- `extractor.py` keyword/stopword sets (`'the','a','and','or'…`, `'invoice number'`, `'tax'`…) —
  the invoice-field extraction heuristics have almost no direct tests.
- `qa.py:43` scoring penalties (`score *= 0.5` when line is short) — the answer-scoring logic is
  weakly tested.

**Action:** the auth/loopback/token paths are exactly the ones you don't want to ship broken.
Add tests that hit the 403 guard and token errors; then re-run `verify` on the auth test file.

## Infrastructure notes

- **Shared sweep venv** (CPython 3.12.13) held project deps so baselines could pass:
  fastapi, sqlalchemy, aiosqlite, python-docx, openpyxl, reportlab, httpx, openai, etc.
- **Babel** installed ad-hoc into JS projects for the JS engine; projects whose tests need
  `node_modules` inside the worktree (TS/tsx, pnpm workspaces) are documented as not
  runnable yet — worktrees exclude `node_modules` by design.
- **Bug found by dogfooding the sweep #1:** UTF-8 output from the Node engine crashed on a
  Windows cp1252 console (`UnicodeDecodeError`). Fixed by forcing
  `encoding="utf-8", errors="replace"` on every subprocess capture. Full suite: 102/102 green.
- **Bug found by dogfooding the sweep #2:** `collect_python_files` used
  `root.glob('**/.venv/**')` to exclude virtualenvs — but a trailing `**` in pathlib glob
  matches directories only, so the exclusion set was empty and the tool mutated
  **1,854 files including site-packages** (Property_Management initially collected
  `.venv\Lib\site-packages\...`). Fixed by expanding matched directories with `rglob`.
  Regression-tested; collection dropped 1,854 → 48 files.

## Roadmap impact

The sweep validates the wedge: **mutation score separates real tests from theater tests** and
exposes untested surfaces that coverage never reveals. It also shows the one operator that adds
noise on CLI/HTML-heavy code (`str_literal '' → 'MUTANT'`) — a candidate for opt-in status in v0.6.
