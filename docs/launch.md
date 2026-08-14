# Launch kit

Ready-to-paste drafts. Repo: https://github.com/rorshopping/mutation-gate

## Hacker News — Show HN

Submit at: https://news.ycombinator.com/submit

**Title:**
```
Show HN: Mutation Gate – a mutation-score gate for AI-written tests
```

**URL:** https://github.com/rorshopping/mutation-gate

**First comment (paste after submitting):**
```
AI agents now write most new tests, and often the same model wrote the code too.
The result is "theater tests": they pass even when the code is broken, and code
coverage can't catch them (a test that calls a function and asserts `not None`
covers every line).

Mutation Gate inverts the problem. Instead of asking "is the code covered?", it
asks "does your test suite FAIL on a deliberately broken program?" It mutates the
source (flips comparisons, removes statements, kills returns...), runs your suite
against each mutant, and reports the mutation score. A suite that scores 60% while
showing 90% coverage isn't a suite — it's theater.

Highlights:
- Python (stdlib `ast`) + JS/TS (bundled Babel engine), 11–12 operators each
- `verify <testfile>`: per-test-file contribution. Real tests ~90%+, the theater
  tests AI writes come in at ~20–28%. This is the wedge: gate it in CI.
- `--verify-changed-tests 0.5`: fails any PR that adds a theater test, even when
  the mutation score itself passes
- `--diff` delta mode, on-disk cache, per-mutant test subsetting, PR comments,
  JUnit/HTML/JSON reports
- `server`/`worker`/`run --remote`: distributed runner, zero deps (stdlib HTTP)
  — OSS core of a future hosted runner
- No LLM calls at any point; it's deterministic. MIT.

Dogfooded on itself: the real test suite in examples/demo scores 91.5%, and the
"AI theater" suite it ships with scores 28% — the tool calls out its own demo.

Would love feedback, especially from people running agent-generated tests in CI
today: does the contribution metric match your intuition? What operators are missing?
```

---

## Reddit

### r/Python (self-post)

**Title:** Mutation Gate: mutation-score gate that catches AI-written "theater" tests

**Body:**
```
AI agents write most new tests now, and often the same model wrote the code too.
Those tests are tautological — they pass even when the code is broken. Coverage
can't see it: a test that calls a function and asserts `not None` covers every line.

Mutation Gate flips the question. Instead of "is the code covered?" it asks "does
your suite FAIL on a deliberately broken program?" It mutates the source (comparison
flips, statement removal, `return` → `return None`, ...), runs your suite against each
mutant, and reports a mutation score.

- `verify <testfile>` gives each test file a *contribution* score: real tests ~91%,
  theater tests ~28%. High reach / low kill = no teeth.
- `--verify-changed-tests 0.5` fails any PR that adds a theater test, even when the
  mutation score passes. That's the CI wedge for agent-generated tests.
- `--diff` delta mode, on-disk cache, per-mutant test subsetting, PR comments,
  JUnit/HTML/JSON, GitHub Action included.
- Distributed runner (`server`/`worker`/`run --remote`) with zero deps.
- Deterministic — no LLM calls at any point. MIT.

https://github.com/rorshopping/mutation-gate
```

### r/node (for the JS/TS angle)

**Title:** Mutation Gate: catch theater tests your AI wrote, for JS/TS too (Babel, npm test)

**Body:**
```
Same idea as the Python post but this targets the JS/TS crowd: a mutation testing
gate that catches "theater" tests — the ones that pass even when the code is broken.

It mutates your JS/TS (comparison flips, statement removal, return → null, ...) with
a bundled Babel engine and runs `npm test` (or your test command) against each mutant.
`verify <testfile>` scores each test file's contribution, so the empty
`assert.ok(result !== undefined)` tests your agent wrote show up as ~20%, not 90%.

- Node's built-in test coverage (LCOV) drives coverage-guided runs + per-mutant
  test subsetting — no extra deps beyond the Babel packages.
- GitHub Action included; posts a mutation report as a PR comment.
- Distributed runner: `server`/`worker`/`run --remote`, zero dependencies.

https://github.com/rorshopping/mutation-gate
```

---

## Lobsters

https://lobste.rs (login required to submit)

**Title:** Mutation Gate: a mutation-score gate for AI-written tests

**URL:** https://github.com/rorshopping/mutation-gate

**Body:** reuse the HN first comment (drop the "Show HN" framing).

---

## X / Twitter (thread)

Post as @rorshopping:

```
1/ AI agents write most new tests now — and often the same model wrote the code.
Result: "theater tests" that pass even when the code is broken. Coverage can't see
it (assert not None covers every line).

Mutation testing can.

2/ mutation-gate mutates your source (flips comparisons, removes statements, kills
returns), runs your suite against each mutant, reports a mutation score.

It ships a `verify` command that scores EACH test file's contribution.
Real tests: ~91%. Theater tests: ~28%. High reach, low kill = no teeth.

3/ The CI wedge: `--verify-changed-tests 0.5` fails any PR that adds a theater test,
even when the mutation score itself passes. Agent-generated tests now have to
actually prove something.

4/ Python (stdlib ast) + JS/TS (Babel). Deterministic, zero LLM calls. Cache, delta
mode, PR comments, GitHub Action. Distributed runner: server/worker/run --remote,
zero deps. MIT.

https://github.com/rorshopping/mutation-gate
```

---

## Dev.to

**Title:** Mutation Gate: catch the theater tests your AI wrote

**Body:** use the r/Python post body, expand with the demo output:

```
$ mutation-gate verify tests/test_real.py     # a real suite
Contribution: 91.5% — Strong

$ mutation-gate verify tests/test_theater.py  # what an agent writes
Contribution: 28.2% — Weak — likely a theater test
```

---

## Notes

- HN ranking tip: post the Show HN in the morning (US Pacific) on a weekday.
- Don't upvote-brigade; one submission is enough. If it gets flagged, wait a day.
- The repo description and topics are already set; CI runs on `main`.
