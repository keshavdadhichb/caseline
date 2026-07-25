# Caseline — Agentic Suspicious-Activity Detection (Hackathon Build)

You are building a query-driven AI agent that detects money-laundering patterns in
transaction data for a bank compliance team. 14-hour build window, 2 developers,
judged by bank engineers on: agentic behavior, explainability, domain relevance,
simplicity, and demo quality. Read this ENTIRE file before writing any code, and
re-read the HARD RULES before every feature.

---

## HARD RULES (never violate)

0. **The product is named Caseline.** Use it consistently everywhere: repo name
   `caseline`, UI header, README title, SAR document header, slides, video.
   Never "the agent", "AML tool", or any old name in user-facing text.

1. **The agent is NOT a fixed pipeline.** Every query goes: parse → structured
   execution plan (JSON) → selective tool invocation. If a query asks about
   "structuring in the last 30 days", the agent must filter to 30 days and skip
   irrelevant tools — and the plan must show that it skipped them. A linear
   load→EDA→model→output flow for every query is a failure.
2. **Every flag has a reason.** No detection output exists without a plain-English
   explanation citing the typology by name and the evidence (counts, amounts, window).
3. **Explainability over model complexity.** Hybrid rules + IsolationForest. No deep
   learning, no training pipelines, no GPU anything. Simple and inspectable wins.
4. **Ship in vertical slices.** End-to-end working (query → plan → result → UI)
   before adding any new capability. Never leave main broken.
5. **Commit style:** small, frequent, descriptive, conventional-commit format
   (`feat(agent): dynamic tool selection in planner`). Commit after every working
   unit, roughly every 20–30 minutes. Never squash history.
6. **Design rules (frontend section below) are law.** If a generated component
   violates them, fix it immediately — do not accumulate drift.

---

## Architecture

```
frontend/   React 18 + Vite + Tailwind + shadcn/ui + react-flow
backend/    Python 3.11 + FastAPI + pandas + scikit-learn + networkx
data/       transactions CSV (committed sample ≤50MB) + synthetic ring injector
evals/      query test suite + baseline comparison script
```

- LLM: Anthropic API (claude-sonnet-4-6) for query parsing/planning and SAR
  drafting ONLY. All detection math is deterministic Python — the LLM never
  computes risk scores. Key from `ANTHROPIC_API_KEY` env var, never committed.
- Backend serves REST on :8000, frontend dev server on :5173 with proxy.
- One command to run each side: `make backend`, `make frontend`. Write the
  Makefile in the first hour.

## Data

- Dataset: IBM Transactions for Anti-Money Laundering (Kaggle, HI-Small variant)
  — or SAML-D if IBM is unavailable. Load into pandas; normalize columns to:
  `txn_id, ts, from_account, to_account, amount, currency, channel, label,
  laundering_type`.
- `data/inject_ring.py`: injects one documented synthetic smurfing ring
  (9 mule accounts → 1 aggregator, deposits ₹/$ just under the 10,000 reporting
  threshold over 6 days) with a fixed seed. Clearly marked as synthetic in code
  comments and README. This guarantees a narratable catch for the demo.

## Agent design

**Planner** (`backend/agent/planner.py`): one LLM call. Input: user query +
tool catalog (name, description, params). Output: strict JSON:

```json
{
  "intent": "detect_structuring",
  "filters": {"window_days": 30, "min_amount": null, "accounts": null},
  "typologies": ["structuring"],
  "steps": [
    {"tool": "filter_data", "params": {...}, "reason": "query scopes to 30d"},
    {"tool": "feature_engine", "params": {...}, "reason": "..."},
    {"tool": "rules_engine", "params": {...}, "reason": "..."}
  ],
  "skipped": [{"tool": "graph_analysis", "reason": "no network pattern requested"}],
  "clarification_needed": null
}
```

If the query is ambiguous (no time window on a time-dependent ask, etc.), set
`clarification_needed` to a single question instead of guessing — the UI renders
it and the user answers.

**Executor** (`backend/agent/executor.py`): runs steps in order, records a trace
event per step (`pending → running → done`, with a one-line result summary),
streams trace events to the frontend via SSE (`/api/query/stream`).

**Tools** (`backend/tools/`, each a pure function with a docstring the planner sees):
1. `filter_data` — date/account/amount/channel scoping
2. `profile_data` — quick EDA summary (only when the query asks for overview)
3. `feature_engine` — per-account rolling features: 7d/30d txn count & sum,
   amount z-score vs own history, velocity (txns/hour), % of txns within 10%
   below the 10,000 threshold, rapid in→out ratio
4. `rules_engine` — named typology rules, each returning (flag, evidence):
   - STRUCTURING: ≥3 txns within 10% below threshold inside 7 days, same account
   - VELOCITY: txn count > 4σ above account's own baseline
   - RAPID_MOVEMENT: ≥80% of inbound funds moved out within 48h
   - HIGH_RISK_AMOUNT: amount z-score > 4 vs account history
5. `anomaly_model` — IsolationForest on the feature matrix (fit once at startup,
   cached); returns anomaly score per account
6. `graph_analysis` — networkx directed graph: fan-in detection (≥5 distinct
   senders → 1 receiver in ≤7 days, receiver then consolidates out), cycle
   detection ≤5 hops for round-tripping, connected components to group rings
7. `risk_scorer` — combines rule hits + anomaly score + graph findings into
   LOW/MEDIUM/HIGH with a weighted, printed formula (no black box)
8. `case_builder` — assembles the case file: entity, typology, evidence table,
   timeline, ring subgraph (nodes/edges JSON), recommended action
   (monitor / review / report)
9. `sar_drafter` — LLM call that turns a case file into a 150–250 word SAR
   narrative (who, what, when, pattern, amounts, recommendation). Deterministic
   input → the narrative cites only evidence present in the case file.

**Hybrid scoring is the story:** rules give precision + explainability, the
model catches what rules miss, the graph catches networks. The risk_scorer
explanation must say which of the three fired.

## API contract (freeze in hour 0 so UI and backend stay in sync)

Async by design — POST returns immediately so the trace panel can show live
progress; results are fetched when the run completes:

- `POST /api/query` `{query: string, clarification_answer?: string}` →
  returns immediately: `{trace_id, plan, clarification_needed}` (if
  clarification_needed is set, no execution starts — UI shows the question)
- `GET  /api/query/{trace_id}/trace` → `{status: running|done|error,
  events: [...]}` — UI polls every 500ms (or SSE upgrade if trivial)
- `GET  /api/query/{trace_id}/results` → `{results, cases}` — valid once
  status=done; UI calls it when the trace reports completion
- `GET  /api/case/{case_id}` → full case file
- `GET  /api/case/{case_id}/export` → single-page PDF (weasyprint or
  browser-print CSS — do not burn >30 min on PDF)
- `GET  /api/health`

Execution runs in a background task (FastAPI BackgroundTasks is enough); trace
events append to an in-memory store keyed by trace_id. evals/run.py uses the
same flow: POST, poll trace until done, fetch results, assert.

Also read `HACKATHON.md` (repo root, GITIGNORED — never commit it) for the
problem-statement context: the 3 canonical queries, the judges' definition of
agentic, required capabilities, and their architecture vocabulary. The query
chips, evals q01–q03, and the demo use those 3 queries verbatim. Alias one
labeled/interesting account to "customer ID 4521" in data/prepare.py so query
3 resolves richly; document the alias in DATA.md. Currency displays in USD.

## Frontend design system (LAW — put violations right immediately)

Aesthetic: "compliance case desk" — light, institutional, calm, precise. It must
look like an internal tool a bank ships, not a hackathon demo.

Tokens (define once in Tailwind config, never inline arbitrary colors):
- `--bg: #F7F8FA` (near-white, cool) · `--surface: #FFFFFF`
- `--ink: #16181D` (primary text) · `--muted: #5B6472`
- `--accent: #1F3A5F` (deep navy — interactive elements only)
- `--risk-high: #B3261E` · `--risk-med: #B45309` · `--risk-low: #1E7B4F`
- Hairline borders `#E4E7EC`, radius 6px, shadows barely-there or none.

Rules:
- **Color means risk. Nothing else on screen may use red/amber/green.**
- Type: Inter for UI; JetBrains Mono for account IDs, txn refs, and the entire
  trace panel; `tabular-nums` on every numeric column; currency formatted
  properly, never raw floats.
- One screen, three zones: query bar with the 3 example-query chips (verbatim
  from the problem statement) · results table with expandable case-file rows ·
  right-side execution-trace panel.
- Motion budget: the trace panel streaming is the ONLY animation. Skeletons for
  loading (never spinners). Nothing reflows or jumps.
- Empty state teaches: example chips + one line on what the agent can do.
- Buttons say what they do: "Escalate to SAR", "Export case file".
- Banned: dark mode, gradients, glassmorphism, emoji icons, purple, decorative
  charts, more than the two typefaces above.
- Must be fully readable at 1280×720 (projector). Test at that size.
- Ring graph (react-flow): grey nodes, red edges only on suspicious paths,
  click node → highlight its flows. No physics chaos.

## Git workflow (judged on commit history — keep it honest and clean)

Claude Code runs on Keshav's machine only. Keshav authors ~85–90% of the work.
The teammate contributes her lane (see TEAMMATE_TASKS.md) through the GitHub
web editor from her own logged-in account — her commits are genuinely hers.
Never author commits for another person.

- Branches: `feat/<unit>`, one per milestone deliverable (`feat/planner`,
  `feat/trace-panel`). Her web-editor work lands on `docs/<unit>` branches
  (`docs/eval-queries`, `docs/typologies`) via PRs she opens herself.
- Commit cadence: after every working unit, target every 20–30 min.
  Conventional commits describing WHAT and WHY:
  `feat(rules): structuring detection — flags >=3 txns within 10% below limit in 7d`
  Never `wip`, `fix stuff`, `updates`.
- PRs: Keshav opens PRs for major units and merges after self-review notes
  (a one-line "reviewed: checked X" comment is fine solo). The teammate's PRs
  are reviewed and merged by Keshav with a real comment; Keshav's README/eval
  PRs can be reviewed by her from the browser — genuine cross-review where it
  genuinely happened.
- Contract/Makefile/this file change only via `chore(contract):` commits.
- Main is always green. First commit within 10 minutes of start.
- README carries an honest contribution note: Keshav — architecture, agent,
  detection, frontend; teammate — eval suite, domain research, documentation.

## Resilience & demo insurance (non-negotiable)

- **Planner cache:** persist planner JSON for every query seen (`.cache/plans/`).
  Live LLM call first; on failure or >8s latency, serve cached plan. Pre-warm the
  cache with the 3 problem-statement queries + all demo-script queries. The demo
  must work with wifi off.
- **Dataset sampling:** pre-sample the dataset to ~200k rows, preserving ALL
  labeled laundering transactions and their counterparties. Fixed seed. Document
  the sampling in README. Every query must answer in <10s.
- **SSE fallback:** if SSE integration exceeds 45 min of effort, switch to 500ms
  polling of a trace endpoint — visually identical.
- **Pinned seeds everywhere:** IsolationForest `random_state=42`, ring injector
  seed, sampler seed — `make eval` output must be byte-identical across machines.
- **sar_drafter fallback:** a template-based narrative generator if the LLM call
  fails, so case files are never empty.

### Signature UI moves (build these exactly — this is the "wow" budget)

1. **Trace panel as a live analyst ledger.** Terminal-styled (mono, timestamps,
   hairline rows) but light-themed. Each plan step renders `pending → running →
   done ✓` with a one-line result ("feature_engine: 200,014 txns → 4,102
   account-windows"). **Skipped tools render dimmed with strikethrough and the
   reason** ("graph_analysis — skipped: no network pattern requested"). This
   single component proves agentic behavior visually.
2. **Threshold spark-timeline.** Inside each structuring case: a small
   (~560×120) chart of the account's deposits over time as dots, with the
   10,000 reporting threshold as a dashed line. The pattern — a cluster of dots
   hugging just under the line — is instantly visible. Annotate one dot:
   "9,720 (−2.8% below threshold)". This makes the crime visible at a glance;
   it is the screenshot for slide 1.
3. **Ring graph focus mode.** Click the aggregator node → unrelated edges fade
   to 15% opacity, the ring's edges go risk-red, and a floating chip shows the
   consolidated total ("₹8.4L into ACC-90112 from 9 accounts in 6 days").
4. **SAR draft as a document.** Rendered on a white "paper" surface with a
   case-reference header (case ID, date, analyst: Caseline Agent), body text, and
   two actions: Copy · Export case file. It should look like something filed,
   not a chat bubble.
5. **System integrity strip.** Slim footer: dataset name + row count · model:
   IsolationForest (seed 42) · rules: 4 typologies · evals: 12/12 passing.
   Quiet proof of rigor on every screenshot.
6. **Keyboard**: `/` focuses the query bar, `Esc` closes a case, `Enter` submits.
7. **Micro-transitions only**: 150ms ease on case-row expansion; everything else
   is instant. The trace stream remains the only real animation on screen.

Priority if time runs short: 1 → 2 → 4 → 3 → 5. Never ship 3 without focus mode
half-working — a broken graph is worse than no graph (fallback: static ring
image in the case file).

## Eval harness (`evals/`)

- `evals/queries.yaml`: 12 queries with expected intent, expected tools invoked,
  expected typology in results. Include the 3 example queries from the problem
  statement — those must pass PERFECTLY.
- `evals/run.py`: runs all queries against the API, prints pass/fail table.
- `evals/baseline.py`: naive threshold-rules-only baseline vs the agent on
  labeled data → prints flags count, precision, recall, false-positive rate for
  both. This table goes verbatim into the README.
- `make eval` runs everything.

## Milestones (14h, solo build — checkpoint at every mark, commit constantly)

- **H0–1 Foundation:** repo scaffold, Makefile (`setup/backend/frontend/eval`),
  `data/prepare.py` run (normalized 200k sample committed), ring injector,
  API contract stubbed with canned responses, frontend Vite+Tailwind+shadcn
  scaffold with design tokens configured. Two commits minimum in this hour.
- **H1–3 Detection core:** feature_engine + rules_engine (all 4 typologies),
  unit tests proving the injected ring and known IBM-pattern rows are caught.
- **H3–5 Agent:** planner (LLM → strict JSON, with cache layer) + executor +
  trace-events endpoint (polling; upgrade to SSE only if trivial), wired to
  filter/features/rules. One end-to-end query works via curl.
  **CHECKPOINT: the 3 problem-statement queries produce visibly different
  plans with different tools invoked/skipped.**
- **H5–6.5 Frontend shell:** query bar + example chips + results table + risk
  pills + skeletons + empty state, live against the real API.
- **H6.5–8 Hybrid complete:** anomaly_model + graph_analysis + risk_scorer with
  printed formula. Ring detection returns the injected ring's subgraph JSON.
- **H8–9 Case files:** case_builder + sar_drafter (template fallback) + SAR
  document UI + export.
- **H9–10.5 Signature UI:** trace ledger panel + threshold spark-timeline.
- **H10.5–11.5 Evidence:** eval harness wired to `evals/queries.yaml`
  (teammate's PR — review and merge it here), baseline.py comparison, numbers
  pasted into README.
- **H11.5–13 Hardening:** ring graph UI with focus mode, 20 adversarial
  queries, clarification flow, every query <10s, full demo run with wifi off.
- **H13–14 Ship:** README product page assembled (merge teammate's docs PRs),
  fresh-clone test, hero GIF + screenshots, final polish commit.

Scope-cut order if any milestone slips >30 min (solo margins are thin):
PDF export → SSE (keep polling) → ring-graph focus mode (keep static ring
image) → cycle detection (keep fan-in) → profile_data tool → evals 12→8.
NEVER cut: planner dynamism, trace panel, rules engine, spark-timeline,
baseline comparison, README.

## README (a product page, not docs)

Top to bottom: hero GIF of a query being answered with the trace streaming →
one-paragraph pitch → the baseline-vs-agent results table → architecture diagram
(mermaid) → the four typologies with one-line definitions → setup (exactly:
clone, `make setup`, `make backend`, `make frontend` — and it must actually work
on a fresh clone) → eval instructions (`make eval`) → dataset citations →
AI-tool disclosure (Claude Code, Anthropic API, any video tooling) → synthetic
ring disclosure → team contribution split.

## Definition of done (before final commit)

- The 3 example queries from the problem statement produce correct, explained,
  beautiful results with visible dynamic plans (different tools invoked/skipped
  per query).
- The injected smurfing ring is caught via graph fan-in, shown as a ring graph,
  and produces a complete case file with SAR draft.
- `make eval` passes 12/12; baseline table shows large false-positive reduction.
- Fresh clone runs on a second machine with only the README.
- No secrets in history, no dead code, no TODOs on main.
