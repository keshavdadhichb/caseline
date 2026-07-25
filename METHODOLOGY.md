# Caseline — Detection Tuning Methodology

This document records a full pass at fixing Caseline's detection numbers,
which were poor going in: 15,440 flags, 16.1% precision, 39.0% recall
against a naive baseline that gets 52.1% recall. The mandate for this pass
was explicit and is repeated here because it shaped every decision below:
**every threshold change had to be justified by AML domain reasoning
written down before it was measured — no sweeping parameter values and
keeping whichever scores best.** Comparisons during tuning used the dev
split only; the numbers in this document's tables were computed once, on
the held-out test split, at the end.

---

## Step 0 — Diagnosis before any change

The working hypothesis going in was that `TESTING.md`'s "15,440 flags"
figure might be a transaction/account unit mismatch — if the baseline
compared different units, every metric reported so far would be
meaningless, and that would be the actual bug.

**Verdict: no unit mismatch exists.** Every quantity in `evals/baseline.py`
is consistently account-level:

| Quantity | Unit | Verified |
|---|---|---|
| `ground_truth_accounts()` | accounts | independently recomputed from `df[df.label==1]`'s `from_account`/`to_account` union — exact match |
| `baseline_flagged_accounts()` | accounts | `{from_account, to_account}` union of large-amount rows |
| `agent_flagged_accounts()` (now `risk_scorer` output) | accounts | `RiskRecord.account_id` |
| `universe_size` | accounts | `{from_account, to_account}` union of the whole frame |

Measured base rates (full 200,029-row / 150,971-account committed sample):

| Metric | Count | Rate |
|---|---|---|
| Transactions | 200,029 | — |
| Accounts | 150,971 | — |
| Labeled laundering transactions | 5,206 | 2.603% of transactions |
| Accounts touching a labeled laundering transaction | 6,368 | 4.218% of accounts |
| — as sender only | 2,382 | |
| — as receiver only | 2,982 | |
| — as both | 1,004 | |

Since no unit bug existed, the numbers did **not** improve "for free" —
every subsequent change was a genuine tuning decision, not a bug fix
disguised as one. (Three unrelated real bugs *were* found and fixed later,
but not this one — see the "bugs found" section below.)

**A related methodological note, not a bug**: `ground_truth_accounts()`
labels an account "positive" if it merely appears as sender *or* receiver
of any labeled transaction — meaning a legitimate counterparty who once
received a payment from a launderer counts as ground truth. This is
standard practice for AML evaluation (network exposure is itself a real
review signal), but it means "recall" here measures "accounts touched by
laundering," a somewhat easier target than "accounts that are themselves
laundering" — worth knowing when reading the recall numbers below.

---

## Step 1 — Dev/test split

`evals/split.py`, seed 42, deterministic (verified byte-identical across
two runs). **Split by account, not by transaction row** — this is the one
design decision in this document that isn't a threshold choice but is
worth explaining, because getting it wrong would have invalidated
everything else: detection rules are inherently relational (STRUCTURING
needs an account's full 7-day deposit history; FAN_IN_RING needs a
receiver's full set of senders). A naive random split of transaction rows
would scatter a single account's pattern across both halves — e.g. 3 of a
structuring account's 5 qualifying deposits landing in dev and 2 in test —
degrading detection in *both* splits as a pure artifact of the split
itself. That artifact would have been indistinguishable from "the
tightened rules are too strict" while tuning against dev, which is exactly
the failure mode this whole exercise exists to avoid.

So: every account is assigned to exactly one of {dev, test}, stratified on
whether it touches labeled laundering (both splits get a proportional
positive share), with the injected ring's 11 accounts force-assigned to
test. A transaction belongs to a split if either endpoint is assigned to
it (an account's activity is never split across dev/test, even though a
transaction whose two accounts land in different splits can appear in
both).

| Split | Accounts | Rows | Positive rate |
|---|---|---|---|
| dev (40%) | 60,383 | 116,372 | 4.21% |
| test (60%) | 90,588 (incl. the 11 ring accounts) | 169,728 | 4.22% |

---

## Step 2 — The anomaly model's calibration

The brief assumed `IsolationForest(contamination=0.1)` was mechanically
flagging ~10% of rows and was the dominant false-positive source. Both
halves of that premise turned out to be wrong, checked before acting on
either:

1. **The code never sets `contamination=0.1`.** It uses no explicit value
   at all, which means sklearn's actual default applies —
   `contamination='auto'`, not `0.1` (confirmed: `sklearn==1.9.0`).
2. **Changing `contamination` has zero effect on this pipeline's output,
   regardless of value.** `decision_function()` = `score_samples()` −
   `offset_`, and `offset_` is derived from `contamination` at fit time —
   but `normalize_anomaly_score()` re-normalizes every raw score against
   the *population's own median and p99 of that same decision_function
   output*. Any constant shift `contamination` induces in `offset_` (and
   therefore in every account's raw score) shifts the population median
   and p99 by exactly that same constant, canceling out of
   `(score − median) / (p99 − median)` identically. Verified empirically,
   not just algebraically: refit with `contamination` in
   `{'auto', 0.1, 0.02, 0.005}` and count accounts crossing a fixed
   normalized threshold —

   ```
   contamination='auto': n(norm>=0.5) = 12,060 (7.99%)
   contamination=0.1   : n(norm>=0.5) = 12,060 (7.99%)
   contamination=0.02  : n(norm>=0.5) = 12,060 (7.99%)
   contamination=0.005 : n(norm>=0.5) = 12,060 (7.99%)
   ```

   Identical to the row. Setting `contamination` "to the measured base
   rate" would have looked like a principled fix and changed nothing.

**The actual lever** is the threshold applied to the normalized score —
the old `ANOMALY_CANDIDATE_FLOOR = 0.5`, an unexamined constant on an ad
hoc `[median, p99] -> [0,1]` scale that let **~8% of all 150,971
accounts** in as anomaly-only candidates, nearly double the 4.22% measured
base rate, on the *least* corroborated tier. Replaced with
`ANOMALY_TOP_PERCENTILE = 95.0` (`anomaly_model.py`): only accounts in the
population's own top 5% of raw anomaly score qualify as "anomalous enough
to matter without a rule or graph hit." 95.0 (not the unrounded 95.78th
percentile) is a clean cutoff in the neighborhood of the measured 4.22%
base rate, not a value chosen because it scored well — it was fixed before
any tier-level metric was computed.

This is the direct, correctly-targeted version of the brief's original
intent: base-rate-derived calibration of the anomaly signal, applied to
the parameter that actually controls it.

---

## Step 3 — Rule tightening (FATF-mapped rationale, written before measuring)

All four rules live in `backend/tools/rules_engine.py`; the reasoning
below is transcribed from that file's module docstring, which is the
source of truth kept next to the code.

### STRUCTURING — FATF "Structuring / Smurfing"

**Before:** ≥3 transactions within 10% of the $10,000 threshold in 7 days,
either sender or receiver side, no further condition.

**After:** ≥5 deposits (not 3) in a 5% band (not 10%), **receiver side
only**, AND ≥60% of that total consolidated back out within a further 7
days.

**Why:** deliberately staying under a reporting threshold is not itself
the crime — a legitimate cash business (a corner store depositing $9,200
most days) does that every week. What distinguishes structuring from
ordinary commerce is what happens to the money *after* it accumulates: a
legitimate business leaves it in the account; a smurfing aggregator
consolidates it out again quickly. The consolidation leg is the part that
actually separates crime from commerce, so it's now required, not optional
— and because "accumulate then move it out" is inherently about what an
account does with money it *receives*, the rule is now scoped to the
receiver side.

**Consequence, stated plainly:** a mule who only ever *sends* 3-5
sub-threshold deposits and does nothing further no longer trips this rule
on their own — only the aggregator, who receives and then consolidates,
does. On the current dataset, checked directly: **no real (non-synthetic)
account satisfies the tightened rule at all** — only the injected ring's
aggregator (4521) does. The 9 mules are still part of the case file via
`FAN_IN_RING`'s ring subgraph (graph_analysis names all 9 in evidence and
the case visualization), just not flagged individually by this rule
anymore. This is the largest single tradeoff in this document — see the
verdict at the end.

### VELOCITY and HIGH_RISK_AMOUNT — general "deviation from established
pattern" red flags (not a single named FATF typology; both are
cross-typology monitoring indicators)

**Before:** 4σ above/vs. an account's own history, evaluated regardless of
how much history existed.

**After:** same 4σ bar, but only evaluated once an account has
`MIN_HISTORY_FOR_BASELINE = 10` prior transactions.

**Why:** a standard deviation computed from a handful of points is not a
statistically stable baseline — with 3-4 transactions, one ordinary busy
hour or one modestly large payment looks like an extreme outlier purely
from small-sample noise, not because behavior actually changed. Requiring
enough history before trusting a z-score is standard practice for any
such rule, not a tuned choice; 10 is a conventional, round minimum-sample
threshold, not a fitted one.

### RAPID_MOVEMENT — FATF "Layering" via funnel/pass-through accounts

**Before:** ≥80% of inbound moved out within 48h, with a $1,000 minimum
inbound (already added in an earlier pass, kept — see below).

**After:** same two conditions, **plus ≥2 distinct inbound senders**
(`RAPID_MOVEMENT_MIN_SOURCES`, using a new `feature_engine.py` column,
`inbound_sender_count`).

**Why:** a single counterparty whose funds a receiving account promptly
forwards on is routine two-party settlement (an escrow account, a payroll
intermediary) — not what "funnel account" means in the layering typology,
which specifically requires *multiple sources* gathering into one conduit
before it scatters back out. The $1,000 materiality floor was kept
as-is: checked against the dataset's amount distribution (median
transaction $970.62, mean $427,385 — heavily right-skewed) and judged
still a reasonable "not obviously trivial" bar; no new domain reason to
move it, so it wasn't moved.

---

## Step 4 — Risk tiers: corroboration, not a blended score

`backend/tools/risk_scorer.py` was restructured from "weighted sum crosses
a cutoff" to explicit corroboration between detection methods:

```
HIGH   = a named rule fired AND at least one of (graph finding, anomaly
         score in the population's own top tier)
MEDIUM = exactly one detection method fired: a rule alone, or a graph
         finding alone
LOW    = anomaly score alone, with no rule and no graph corroboration
```

The old weighted formula (`0.45×rules + 0.35×graph + 0.20×anomaly`) is
kept, but only for *ranking* — Precision@N (Step 5) needs a continuous
ordering within and across tiers, which a 3-bucket label alone can't
provide. It no longer determines `risk_level`.

### A finding this surfaced, reported rather than silently designed around

Once HIGH could be reached by "rule + anomaly-high" directly, the tier's
real-world composition became visible for the first time: **474 of 510
HIGH accounts (93%) get there via rule+anomaly agreement, only 36 via
rule+graph.** This matters because rules and the anomaly model are *not*
fully independent measurements — `anomaly_model.py`'s feature set
includes `near_threshold_count`, `rapid_inout_ratio`, and `std_amount`,
the same quantities the rules directly threshold on. An account that trips
a rule is mechanically more likely to also score high on the anomaly
model simply because both are built from overlapping raw ingredients, not
because two truly independent detection philosophies happened to agree.
Rule+graph corroboration (genuinely independent data: individual
transaction amounts/timing vs. network topology) is real, strong evidence;
rule+anomaly corroboration is real but structurally weaker than the "two
independent signals" framing implies. Not redesigned here — the brief
specified this exact tier logic — but disclosed clearly, because a bank
engineer evaluating the HIGH tier's credibility needs to know which
corroboration path drove which accounts.

---

## Bugs found and fixed during this pass

Three real bugs, found by actually running the changes against the full
200k-row sample, not by inspection. None were threshold retuning — all
were logic/performance defects, fixed and then re-verified, consistent
with "fix the bug, don't weaken the test."

1. **`_structuring` blew the query budget 15x (129s vs. 8s).** An initial
   implementation kept scanning for a later qualifying window whenever an
   earlier one failed the new consolidation check, running the expensive
   consolidation check per candidate window. Fixed to track the single
   best-by-dollar-total window with O(1)-per-step sliding-window
   bookkeeping (the same pattern `graph_analysis._fan_in` already used)
   and check consolidation once.
2. **Same function also built an outbound-transactions lookup for every
   `from_account` in the whole 150,971-account dataset up front**,
   regardless of whether that account was ever a structuring candidate.
   Added a vectorized quick-reject (≥ min count) before building any
   per-account lookup at all. 12.4s → 6ms for this function alone.
3. **`graph_analysis._fan_in`'s `consolidation_ratio` summed ALL of a
   receiver's outbound history**, unscoped to the fan-in window, as the
   numerator — letting the ratio exceed 1000% for any high-volume hub
   account with substantial *unrelated* outbound activity (real examples
   observed: 593%, 869%, 1119%). This silently inflated `FAN_IN_RING`'s
   real-world false-positive rate (57 flags on the real sample, most
   spurious) and was invisible under the old weighted-sum scorer (graph's
   0.35 weight alone couldn't reach HIGH on its own) — fully exposed only
   once the new tier logic let "rule + graph" reach HIGH directly. Fixed
   by scoping `total_out` to the receiver's outbound activity within
   `FAN_IN_WINDOW_DAYS` of the inbound cluster closing. 57 → 20 flags, and
   4521's own evidence is now exactly right (9 senders, $254,317.25 in,
   85% out — matching the injected ring's actual design) instead of an
   inflated number that happened to still clear the threshold.

Bug 3 is arguably the most consequential finding in this whole pass — it
was pre-existing (not introduced by this tuning effort) and had been
quietly inflating graph-driven flags since `graph_analysis.py` was first
written, masked by the old scorer's design.

---

## Before / after (same TEST split, apples-to-apples)

Obtained by checking out the pre-tightening code (git commit `52730bc`,
before any Step 2-4 change) into a separate worktree and running it
against the *same* `data/eval_splits/test.csv` used for the "after"
numbers — not a different historical run on the full dataset.

| | Flags | Precision | Recall | FPR |
|---|---|---|---|---|
| **Before** (pre-tightening, on test split) | 11,362 | 16.5% | 33.9% | 7.56% |
| **After** (this pass, on test split) | 6,136 | 22.3% | 24.7% | 3.80% |
| Naive threshold baseline (unchanged, on test split) | 32,833 | 8.4% | 49.8% | 23.97% |

Ring detection: **10/11 accounts caught, both before and after** (the
missing one, `RING-EXIT-01`, is a pure terminal receiver with no
suspicious behavior of its own in either version — not a miss worth
chasing). Aggregator 4521 scores HIGH (1.0) in both versions.

### What got worse — stated plainly, as instructed

**Recall dropped from 33.9% to 24.7% (and is now well below the naive
baseline's 49.8%).** One sentence a compliance officer would accept: *we
traded catching roughly one in four labeled-adjacent accounts against
catching roughly one in three, in exchange for cutting false alarms
nearly in half (FPR 7.56%→3.80%) and raising the odds that a flagged
account is genuinely worth reviewing by six points — a reasonable trade
if analyst review capacity is the binding constraint, a bad one if missed
cases are the bigger risk.* Whether that's the right trade is a policy
question for the compliance team's risk appetite, not something this pass
can resolve unilaterally.

**STRUCTURING is now nearly silent on real (non-synthetic) data.** Query 1
("Find structuring patterns in the last 30 days") — one of the three
canonical demo queries — now returns exactly **1 result** (4521, MEDIUM
tier, since this narrowly-scoped query plan has no graph/anomaly signal
available to corroborate the rule alone). Not empty — it satisfies the
"never return nothing" bar from the tuning brief — but visibly thinner
than before tightening, when the looser rule caught real IBM-labeled
accounts too. This is a direct, known consequence of the 5-count/5%-band/
consolidation requirement, not a bug; the account it does show (the
demo's own ring) is a strong, well-evidenced result. It's the single most
visible tradeoff of this whole pass and the first thing a live demo
audience would notice if they typed that exact query.

---

## Held-out test-split metrics (measured once, see `evals/baseline.py`)

### 1. Global comparison

| System | Flags | Precision | Recall | FPR |
|---|---|---|---|---|
| Naive threshold baseline | 32,833 | 8.4% | 49.8% | 23.97% |
| Caseline (hybrid) | 6,136 | 22.3% | 24.7% | 3.80% |

### 2. Per-tier metrics

| Tier | Flags | Precision | Recall | FPR |
|---|---|---|---|---|
| HIGH only | 349 | **73.1%** | 4.6% | 0.07% |
| HIGH + MEDIUM | 765 | 51.2% | 7.1% | 0.30% |
| Any flag (HIGH+MEDIUM+LOW) | 6,136 | 22.3% | 24.7% | 3.80% |

### 3. Precision@N (alert triage capacity)

| N | Hits | Precision |
|---|---|---|
| 50 | 50 | **100.0%** |
| 100 | 100 | **100.0%** |

### 4. Pattern-level detection (370 individual attempts, `HI-Small_Patterns.txt`)

Grouped by individual BEGIN/END attempt block, not collapsed to typology
name — collapsing would let one caught attempt of a typology count for
every attempt of that typology and overstate coverage.

| Typology | Detected / Applicable |
|---|---|
| BIPARTITE | 40/49 |
| CYCLE | 48/54 |
| FAN-IN | 35/40 |
| FAN-OUT | 38/48 |
| GATHER-SCATTER | 43/51 |
| RANDOM | 30/41 |
| SCATTER-GATHER | 37/44 |
| STACK | 32/43 |
| **Total** | **303/370 (81.9%)** |

Injected synthetic ring: **10/11 accounts flagged, aggregator 4521 caught.**

### 5. Operational alert volume

- 6,136 total candidates surfaced (46.84 per 1,000 accounts)
- 765 HIGH+MEDIUM ("worth a look") — 5.84 per 1,000 accounts
- Test split spans 17 days → **~45 HIGH+MEDIUM reviews/day** estimated

---

## Step 6 — Confirmation nothing broke

- Full backend test suite: **116/116 passed**, stable across two
  consecutive full runs (fixtures and existing-dataset tests updated
  throughout to match the new rule scope and tier semantics).
- `make eval`: **12/12 passed**, run twice against a live server.
- `evals/baseline.py`: byte-identical output across two runs.
- All 3 canonical queries return non-empty, substantive results — verified
  directly, not just via the eval assertions (query 1 and 2's thinness is
  disclosed above, not hidden behind a passing test).
- The injected ring: 10/11 accounts, complete case file (10-node ring
  subgraph, `recommended_action: "report"`), and a live-drafted SAR
  narrative — verified end to end against a running server.

---

## Verdict

**Are these numbers defensible in front of bank engineers?** Yes, with the
STRUCTURING tradeoff stated up front rather than discovered by a pointed
question. The story is coherent and each number traces to a written
reason: 73.1% precision at HIGH, 100% Precision@50/100, 81.9% pattern
coverage across all 8 typologies, and a real (not cosmetic) fan-in bug
fixed along the way that had been quietly inflating graph-driven flags
since before this tuning pass started. The recall drop against the naive
baseline is real and disclosed, not buried in an appendix.

**Which single number would this session least want to be asked about?**
Not the recall drop — that's an intelligible, defensible precision/recall
trade with a one-sentence answer a compliance officer would accept. The
uncomfortable one is buried in Step 4: **474 of 510 HIGH-tier accounts
(93%) reach HIGH via rule+anomaly agreement, not rule+graph** — and
because the anomaly model's own features overlap with what the rules
directly check, that "two independent signals" story is weaker than it
sounds for the large majority of the HIGH tier. A sharp bank engineer who
asks "how independent are your two corroborating signals, really?" doesn't
have a fully comfortable answer today. The honest fix — either giving the
anomaly model a feature set that's genuinely disjoint from the rules', or
weighting rule+graph corroboration above rule+anomaly in the tier logic —
is a real next step, not something resolved in this pass.
