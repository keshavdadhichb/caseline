# Caseline — Backend Test Report

Full test pass over the backend: fixtures with hand-derived expected values,
live-LLM planner/SAR coverage, a real running-server API contract and
end-to-end suite, resilience checks (no API key, slow LLM, timing, memory),
and a manual data-layer/eval/hygiene pass. Every bug below was found by
actually running something, not by inspection — where a test failed, the
underlying code was fixed and the test re-run, not weakened.

**Total: 140 tests** (111 in the default `make test` sweep + 29 real-network
tests gated behind `pytest.mark.live`, run via `make test-live`).

## 1. Summary by section

| # | Section | Tests | Result |
|---|---|---|---|
| 1 | Fixtures | — | 108-txn/41-account handcrafted dataset, isolated per-typology scenarios (`backend/tests/fixtures.py`) |
| 2 | Data layer | 9 fast + 3 slow (skip if no `data/raw/`) | 12/12 pass |
| 3 | Tools (fixture-based, exact values) | 58 | 58/58 pass |
| 4 | Planner (live) | 10 | 10/10 pass |
| 5 | Executor / trace | 6 | 6/6 pass |
| 6 | API contract (live server) | 9 | 9/9 pass |
| 7 | End-to-end (live) | 4 | 4/4 pass |
| 8 | Evals / baseline | 4 automated + 2 manual runs | 4/4 pass; manual runs 12/12, byte-identical ×2 |
| 9 | Resilience (live) | 5 | 5/5 pass |
| 10 | Hygiene | manual scan | clean |
| — | Pre-existing suite (`test_rules_engine`, `test_hybrid`, `test_case_builder`, `test_sar_drafter`, `test_planner`, `test_executor`, `test_api`) | 31 | 31/31 pass (30 from before this pass + 1 new regression test added to `test_planner.py`, §5 finding 3) |

58 (§3) + 12 (§2) + 6 (§5) + 4 (§8) + 31 (pre-existing) = **111 fast tests** —
matches `pytest`'s own collection count exactly.

Fast suite (`make test`, no network): **111/111 passed** in ~110s.
Live suite (`make test-live`, real Anthropic API calls): **29/29 passed**
in ~10 minutes total across the separate runs performed this session.

## 2. Per-query timing (12 eval queries, real 200k-row sample, live server)

| Query | Time | Under 10s budget? |
|---|---|---|
| q01 — Find structuring patterns in the last 30 days | 9.33s | yes |
| q02 — Which customers made 10+ transactions under $10,000? | 9.22s | yes |
| q03 — Is customer ID 4521 suspicious? | 8.41s | yes |
| q04 — Show me accounts with unusual transaction velocity in the past week | 8.41s | yes |
| q05 — Which accounts move most of their incoming funds out within 48 hours? | 10.37s | **no** |
| q06 — Are there any rings where several accounts funnel money into one account? | 12.71s | **no** |
| q07 — Detect any circular round-tripping transaction patterns | 12.28s | **no** |
| q08 — Give me an overview of the transaction data | 8.29s | yes |
| q09 — Find structuring activity between Sep 10–17, 2022 | 8.41s | yes |
| q10 — What about account RING-M01 — anything concerning? | 8.48s | yes |
| q11 — Is there suspicious activity? | 8.10s | yes |
| q12 — Flag transactions unusually large for the account | 9.42s | yes |

**3 of 12 queries land at or above the 10s budget.** Every one of them is
dominated by the planner's ~8s live-call ceiling (this session's own fix,
see §4) plus graph_analysis's own cost on top for the ring/cycle queries.
This is a real, disclosed tension, not hidden: the 8s planner budget and the
10s total-query budget leave very little room for execution when a query
also needs the graph. Two honest mitigations, not applied here since both
are product decisions: shrink `LIVE_TIMEOUT_SECONDS`, or make planning
genuinely asynchronous (see §5, finding 5) so its latency doesn't eat into
the execution budget at all.

## 3. Eval suite

`evals/run.py` against a live server, run twice: **12/12 passed both times,
byte-identical PASS/FAIL output.** (The underlying LLM plan JSON can vary in
wording between calls — observed directly in `.cache/plans/` diffs this
session — but the structural assertions evals/run.py actually checks, tool
sets and result facts, are stable either way.)

## 4. Baseline comparison (fresh run against the current dataset)

| System | Flags | Precision | Recall | False-Positive Rate |
|---|---|---|---|---|
| Naive threshold baseline | 39,112 | 8.5% | 52.1% | 24.75% |
| Caseline (hybrid) | 15,440 | 16.1% | 39.0% | 8.96% |

`compute_metrics`' precision/recall/FPR formulas independently verified
against hand-worked examples (`test_evals_baseline.py`), including the 0/0
guard cases. Both scripts run twice, byte-identical output both times.

Two honest notes, not hidden:
- Caseline's flagged-account count (15,440) is slightly lower than a
  previously-recorded 15,504 — expected, traced to this session's cycle-
  detection fix (§5, finding 1) removing false-positive 2-hop flags.
- Caseline's recall (39.0%) is genuinely **lower** than the naive baseline's
  (52.1%). This is not a collapse — it's the expected precision/FPR
  tradeoff of being far more selective (5x precision, ~⅓ the FPR) — but
  it's a real number, reported as measured rather than tuned to look better.
  Whether that tradeoff is the right one for a compliance team's risk
  appetite is a product conversation, not a test question.

This table isn't in README.md yet — the README is still an intentional
work-in-progress stub (a later milestone), so there's no stale-number risk
today; these are the current figures for whenever it's filled in.

## 5. Bugs found and fixed this pass

1. **`graph_analysis._cycles` flagged any 2-node reciprocal payment as a
   "cycle"** (A pays B, B pays A — e.g. ordinary bill-splitting). Caught by
   two "must stay clean" fixture accounts that happened to pay each other
   back and forth. Not genuine round-tripping/layering in the sense
   CLAUDE.md describes. Fixed: cycles now require ≥3 distinct hops.
   (`backend/tools/graph_analysis.py`)

2. **`prepare.py`/`inject_ring.py` used a non-stable sort** (`sort_values("ts")`
   defaults to quicksort). At 200k rows / minute-granularity timestamps,
   ties are common, and re-running `inject_ring.py` against its own prior
   output (by design, for idempotency) could reshuffle tied rows
   differently each time — which matters because IsolationForest's seeded
   fit is row-order-sensitive, threatening the "`make eval` output must be
   byte-identical across machines" claim. Fixed: `kind="mergesort"` in both
   files. **Not** regenerated the already-committed
   `data/sample/transactions.csv` (confirmed its current row order predates
   this fix) — every eval/risk-score number already verified against it
   stays valid; regenerating now, hours from a deadline, risks shifting
   borderline (non-4521) scores for no demo-visible benefit. Safe to
   regenerate later. (`data/prepare.py`, `data/inject_ring.py`)

3. **No enforced timeout on the planner's live LLM call.** The Anthropic
   SDK's own default read timeout is 600s, and nothing else bounded it —
   the `elapsed > LIVE_TIMEOUT_SECONDS` check only fires *after* a call
   already returned. A hung/degraded connection (more likely at a demo
   venue than clean wifi-off) could block `POST /api/query`'s synchronous
   handler for up to 10 minutes. Fixed: `timeout=LIVE_TIMEOUT_SECONDS`
   passed to `messages.create`. (`backend/agent/planner.py`)

4. **Fix #3 alone wasn't enough — the SDK retries.** `max_retries=2` by
   default means a per-call timeout still allows up to 3 attempts. Measured
   live: ~25s instead of the intended ~8s. Fixed: client constructed with
   `max_retries=0`. Confirmed live: 25.44s → 8.34s for the same query, with
   all 10 live planner tests and the live E2E/API-contract suites still
   passing (pre-cached demo queries stay fully robust either way — the
   cache-fallback path doesn't care how the live call failed).
   (`backend/agent/planner.py`)

5. **Real logic bug in the "slow-but-successful" cache branch.** Documented
   intent: a live call that succeeds but takes >8s should still serve the
   *last-known-good cached* plan for this run (while saving the fresh one
   for next time). The code wrote the fresh plan to the cache file first,
   then read the file back — so it always read back exactly what it had
   just written, meaning this branch could never serve an old plan; it
   silently behaved identically to just returning the fresh (slow) result.
   Caught by a dedicated test seeding a distinguishable "old" cached plan.
   Fixed: read the existing cache before overwriting it.
   (`backend/agent/planner.py`)

## 6. Real findings, reported but not changed

Each of these is a genuine property of the current system, verified live,
left as-is because fixing it is a product/tuning decision outside the scope
of writing tests — not because it wasn't found.

- **IBM patterns-file typology coverage is ~62%, not 100%.** 1,968 of the
  5,177 real IBM-labeled rows have `label=1` but no matching entry in
  `HI-Small_Patterns.txt`, so `laundering_type` stays `None` for them. A
  property of the upstream Kaggle files, not a Caseline join bug (also
  visible in `prepare.py`'s own startup log line). DATA.md now states the
  rate instead of implying full coverage.

- **`feature_engine.py`'s std calculation isn't internally consistent.**
  `std_amount` (feeds HIGH_RISK_AMOUNT) uses `.std(ddof=0)` explicitly, but
  `hourly_count_std` (feeds VELOCITY) uses pandas' groupby default
  `.agg("std")`, which is `ddof=1`. Documented with the exact closed-form
  z-score for each in `fixtures.py`'s module docstring — not changed, since
  it would shift the calibration of an already-tuned threshold
  (`VELOCITY_SIGMA=4.0`) without a clear signal that the current tuning is
  wrong.

- **`GET /api/case/{id}/export` doesn't exist.** Listed in CLAUDE.md's
  frozen API contract; not implemented anywhere in `app/main.py`. Confirmed
  live (returns 404, not a route). Expected per CLAUDE.md's own scope-cut
  order (PDF export is first to go), but the contract as currently written
  overclaims what's built.

- **Planning is not actually backgrounded.** CLAUDE.md: "POST returns
  immediately so the trace panel can show live progress." In the real
  implementation, `submit_query` calls `plan_query()` — a synchronous, live
  LLM call — *before* handing execution to `BackgroundTasks`. Only
  execution (`run_plan`) is async; planning is not. After fixes #3–#4
  above, this is now a *known, bounded* ~8s wait instead of an unbounded
  one, but it is still not "immediately," and it's the direct cause of §2's
  timing overruns. The architecturally complete fix — backgrounding
  `plan_query` too, with the client polling for the plan the same way it
  polls for results — is a real (if contained) change to the API contract,
  not something to make unilaterally under a test-writing mandate.

- **Executor error-handling has an asymmetry.** Each detection tool's step
  is individually try/excepted — a failure becomes an "error" trace event
  and execution continues past it. The final `case_builder` assembly block
  is *not* wrapped the same way: a failure there propagates out of
  `run_plan` entirely, and is the *only* path that lets a trace's top-level
  `status` ever become `"error"`. Per-tool failures alone can never produce
  a top-level error status, only a per-step one buried in the trace panel.
  Borderline intentional (tool failures are "best effort," final assembly
  is expected to always succeed) — flagged, not changed.

- **Trace/case store memory growth is real but not fully root-caused.**
  `TRACES`/`CASES` are plain dicts with no eviction policy at all. A
  20-query live run showed 615MB–761MB of RSS growth (two separate runs); a
  follow-up 40-query probe sampling every 10 queries showed a *bursty,
  non-monotonic* pattern (+402MB, +224MB, +16MB, +442MB across four
  batches) — more consistent with pandas/numpy's allocator holding arenas
  at a high-water mark than a clean linear per-query leak (which would grow
  smoothly, not flatten mid-run then jump again). Not conclusively
  distinguished from a true leak without a memory profiler. Practically
  relevant either way for a long demo/Q&A session on a laptop; a periodic
  backend restart is a cheap mitigation if that comes up.

## 7. Untested / out of scope (explicit, not hidden)

- **Frontend build/install path.** Untouched this pass — the frontend is a
  minimal scaffold by design, full UI/UX handed off separately. `make
  setup`'s `npm install` step was not exercised in the fresh-clone check
  below.
- **True concurrent-request thread safety.** The isolation verified is
  structural (separate `events` list / dict entry per `trace_id`), not a
  stress test with real concurrent HTTP clients against a live server.
  FastAPI's TestClient runs BackgroundTasks synchronously, so it can't
  exercise this at all; a live-server concurrency test would need actual
  threaded/async clients, which wasn't built this pass.
- **Memory growth root cause**, per §6 — flagged, not diagnosed with a
  profiler.
- **GitHub push** — unrelated to this test pass, still pending per earlier
  session notes (local-only commits, auth blocked).

## 8. Fresh-clone check

Cloned the local repo into a clean directory, followed the backend half of
the README exactly (`make setup`'s Python steps, `make backend`), with no
manual fixes: venv creation, `pip install -r backend/requirements.txt`,
server start, `GET /api/health`, and a real `POST /api/query` for "Is
customer ID 4521 suspicious?" — all worked without touching anything beyond
what the README documents. `data/sample/transactions.csv` and the
pre-warmed `.cache/plans/` entries were present and used correctly straight
from git, with no `data/raw/` needed. Frontend `npm install` was not
exercised (see §7).

## 9. Hygiene

No secrets, keys, or tokens in tracked files (`.env` untracked, only
`.env.example`'s placeholder is present). `HACKATHON.md` and `data/raw/`
correctly absent from tracked files. No sponsor references
(Societe Generale / SocGen / SGGSC / bare "SG") anywhere in tracked
content. No `TODO`/`FIXME`/`XXX` markers, no stray `print()` debugging in
`backend/app`, `backend/tools`, or `backend/agent`, no commented-out code
blocks found in a pattern scan.

## Verdict

**Yes, demo-ready** for the three canonical queries and the ring-detection
story — every one of those paths was verified live, end to end, against a
real running server, more than once, including with the API key removed
entirely. Five real bugs were found and fixed in the process (two of them
in the planner's core resilience path — the exact code meant to protect the
demo), not just theoretical ones caught by inspection.

**The single largest remaining risk is the timing/architecture finding in
§2 and §6**: planning is synchronous inside the POST handler, not
backgrounded, so 3 of the 12 eval queries — specifically the
graph-analysis-heavy ones (fan-in ring, cycle detection) — land at 10.4s to
12.7s, over CLAUDE.md's own 10s-per-query budget. It won't be visible on
the three canonical queries in the demo script (all comfortably under
budget, 8.1–9.4s), but if a judge asks an ad-hoc graph question during Q&A,
it may feel slower than the rest of the demo. The contained fix (shrink
`LIVE_TIMEOUT_SECONDS` further) trades away plan quality on non-cached
queries; the complete fix (background planning too) is a real, if small,
architecture change — both are product calls, not something resolved by
this test pass.
