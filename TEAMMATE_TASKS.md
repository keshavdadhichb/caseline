# Teammate lane — docs & evals (GitHub web editor)

All of this is done from **your own GitHub account in the browser** (repo →
press `.` or use the pencil icon). Create each branch when the editor asks
where to commit ("Create a new branch and start a pull request"). Keshav
reviews and merges. Never commit to `main` directly.

Commit style: `docs(evals): add 12 eval queries with expected tools` — small,
descriptive, no "wip".

---

## Task 1 — branch `docs/eval-queries` → file `evals/queries.yaml`

12 natural-language queries with what the agent is EXPECTED to do. The first
three are fixed (q01–q03 below, keep wording exactly); invent the other nine
to cover: velocity spikes, rapid in–out movement, fan-in rings, cycles, a
broad "overview" query (should trigger EDA), a query with an explicit date
range, one naming a specific account, and 1–2 ambiguous ones (agent should
ask a clarification instead of guessing).

Format per entry:

```yaml
- id: q01
  query: "Find structuring patterns in the last 30 days"
  expect_intent: detect_structuring
  expect_tools: [filter_data, feature_engine, rules_engine, risk_scorer, case_builder]
  expect_skipped: [profile_data, graph_analysis]
  expect_typology: STRUCTURING
- id: q02
  query: "Which customers made 10+ transactions under $10,000?"
  expect_intent: aggregate_threshold
  expect_tools: [filter_data, feature_engine, rules_engine]
  expect_skipped: [anomaly_model, graph_analysis, profile_data]
  expect_typology: STRUCTURING
- id: q03
  query: "Is customer ID 4521 suspicious?"
  expect_intent: entity_lookup
  expect_tools: [filter_data, feature_engine, rules_engine, risk_scorer, case_builder]
  expect_skipped: [profile_data]
  expect_typology: SMURFING
```

## Task 2 — branch `docs/typologies` → file `docs/TYPOLOGIES.md`

One page: the four typologies Caseline detects (STRUCTURING, VELOCITY,
RAPID_MOVEMENT, HIGH_RISK_AMOUNT) plus graph patterns (fan-in ring, cycle).
For each: 2–3 sentence plain-English definition, why banks care (reporting
thresholds, layering), and one real-world citation (FATF / FinCEN links).

## Task 3 — branch `docs/readme-research` → edits to `README.md`

Fill the "Typologies" and "Dataset & citations" sections (link the Kaggle
dataset + license, FATF references) and the team contribution note. Keshav
will have left `<!-- TEAMMATE -->` markers where text is needed.

## Task 4 — review

When Keshav opens a PR labeled `needs-review`, read the diff in the browser
and leave one substantive comment before approving (a real observation — a
question, a caught typo, a suggestion).
