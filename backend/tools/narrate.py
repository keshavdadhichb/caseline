"""narrate — turns a plan + trace events + results into the plain-language
narration the UI thread is built around, DETERMINISTICALLY.

No LLM call here, deliberately. Every sentence and every step detail is
assembled from values that already exist: the planner's own `reason` text,
the executor's own one-line `summary`, and the real rule/model/graph
constants from the tool modules. That means the narration can never claim
a threshold, a count, or a formula the system didn't actually use — the
same "cite only what's substantiated" bar `sar_drafter` holds itself to,
and the reason the demo still narrates correctly with wifi off.

The three-part step detail (Chose / Because / Returned) maps onto data
that is already there:
  Chose    — the parameters this step actually ran with (from plan.filters
             plus the tool's own real constants)
  Because  — the planner's own stated reason for including/skipping it
  Returned — the executor's own one-line result summary
"""

from __future__ import annotations

from tools.anomaly_model import ANOMALY_TOP_PERCENTILE, N_ESTIMATORS, RANDOM_STATE
from tools.feature_engine import FEATURE_COLUMNS
from tools.graph_analysis import (
    CYCLE_MAX_HOPS, FAN_IN_CONSOLIDATION_RATIO, FAN_IN_MIN_SENDERS, FAN_IN_WINDOW_DAYS,
)
from tools.risk_scorer import FORMULA
from tools.rules_engine import (
    HIGH_RISK_AMOUNT_SIGMA, MIN_HISTORY_FOR_BASELINE, NEAR_THRESHOLD_HIGH,
    RAPID_MOVEMENT_MIN_INBOUND, RAPID_MOVEMENT_MIN_SOURCES, RAPID_MOVEMENT_RATIO,
    STRUCTURING_CONSOLIDATION_RATIO, STRUCTURING_CONSOLIDATION_WINDOW_DAYS,
    STRUCTURING_HIGH_LOW, STRUCTURING_HIGH_MIN_COUNT,
    STRUCTURING_MEDIUM_LOW, STRUCTURING_MEDIUM_MIN_COUNT, STRUCTURING_WINDOW_DAYS,
    VELOCITY_SIGMA,
)

# Friendly step labels for the thread. Keys are the real tool names the
# planner emits, so a renamed tool surfaces as its raw name rather than
# silently showing a stale label.
STEP_LABELS = {
    "filter_data": "Filter the transaction set",
    "profile_data": "Profile the dataset",
    "aggregate_data": "Count matching transactions",
    "feature_engine": "Build account features",
    "rules_engine": "Apply typology rules",
    "anomaly_model": "Score anomalies",
    "graph_analysis": "Network analysis",
    "risk_scorer": "Combine risk signals",
    "case_builder": "Assemble case files",
}

# Noun forms for running prose ("ran the typology rules and network
# analysis"), where the imperative STEP_LABELS read awkwardly.
PROSE_NOUNS = {
    "profile_data": "the dataset profile",
    "rules_engine": "the typology rules",
    "anomaly_model": "anomaly scoring",
    "graph_analysis": "network analysis",
    "aggregate_data": "a transaction count",
}


def step_label(tool: str, filters: dict | None = None) -> str:
    """Human label for a plan step. filter_data names its own scope so the
    trace reads like the analyst's intent ("Filter to the last 30 days")."""
    if tool == "filter_data" and filters:
        window = filters.get("window_days")
        accounts = filters.get("accounts")
        d_from, d_to = filters.get("date_from"), filters.get("date_to")
        if accounts:
            return f"Filter to {', '.join(str(a) for a in accounts[:2])}"
        if d_from or d_to:
            return f"Filter to {d_from or 'the start'} through {d_to or 'the end'}"
        if window:
            return f"Filter to the last {window} days"
    return STEP_LABELS.get(tool, tool)


def _chose(tool: str, plan: dict) -> str:
    """What this step actually ran with — real parameters and real constants."""
    filters = plan.get("filters") or {}
    typologies = plan.get("typologies") or []

    if tool == "filter_data":
        parts = []
        if filters.get("date_from") or filters.get("date_to"):
            parts.append(f"{filters.get('date_from') or 'the earliest record'} through "
                         f"{filters.get('date_to') or 'the latest record'}, inclusive")
        elif filters.get("window_days"):
            parts.append(f"the last {filters['window_days']} days of the dataset window")
        if filters.get("accounts"):
            parts.append(f"accounts {', '.join(str(a) for a in filters['accounts'])}")
        if filters.get("min_amount"):
            parts.append(f"transactions at or above ${filters['min_amount']:,.0f}")
        return "; ".join(parts) if parts else "the full committed sample, unscoped"

    if tool == "profile_data":
        return "row counts, account counts, date range, total volume and channel mix"

    if tool == "aggregate_data":
        return ("a plain count of transactions per account inside the requested amount band; "
                "no typology rule and no risk judgement is applied")

    if tool == "feature_engine":
        return f"{len(FEATURE_COLUMNS) - 1} per-account features, including rolling counts, amount z-scores, velocity and rapid in-to-out ratio"

    if tool == "rules_engine":
        active = typologies or ["structuring", "velocity", "rapid_movement", "high_risk_amount"]
        bits = []
        if "structuring" in active:
            bits.append(
                f"structuring, strict: {STRUCTURING_HIGH_MIN_COUNT}+ deposits in "
                f"[${STRUCTURING_HIGH_LOW:,.0f}, ${NEAR_THRESHOLD_HIGH:,.0f}) inside "
                f"{STRUCTURING_WINDOW_DAYS}d plus {STRUCTURING_CONSOLIDATION_RATIO:.0%} consolidated out; "
                f"weaker tier: {STRUCTURING_MEDIUM_MIN_COUNT}+ from ${STRUCTURING_MEDIUM_LOW:,.0f}"
            )
        if "velocity" in active:
            bits.append(f"velocity: peak hourly count above {VELOCITY_SIGMA:.0f}σ of the account's own baseline")
        if "rapid_movement" in active:
            bits.append(
                f"rapid movement: {RAPID_MOVEMENT_RATIO:.0%} of inbound out within 48h, "
                f"min ${RAPID_MOVEMENT_MIN_INBOUND:,.0f} from {RAPID_MOVEMENT_MIN_SOURCES}+ senders"
            )
        if "high_risk_amount" in active:
            bits.append(f"high-risk amount: single txn above {HIGH_RISK_AMOUNT_SIGMA:.0f}σ of the account's own history")
        bits.append(f"z-score rules need {MIN_HISTORY_FOR_BASELINE}+ prior transactions before they fire")
        return "; ".join(bits)

    if tool == "anomaly_model":
        return (
            f"IsolationForest · seed {RANDOM_STATE} · {N_ESTIMATORS} trees, fit once on the full "
            f"population; an account counts as anomalous in the top {100 - ANOMALY_TOP_PERCENTILE:.0f}%"
        )

    if tool == "graph_analysis":
        return (
            f"fan-in rings ({FAN_IN_MIN_SENDERS}+ distinct senders inside {FAN_IN_WINDOW_DAYS}d with "
            f"{FAN_IN_CONSOLIDATION_RATIO:.0%}+ consolidated onward) and round-trip cycles up to {CYCLE_MAX_HOPS} hops"
        )

    if tool == "risk_scorer":
        return FORMULA

    if tool == "case_builder":
        return "entity, typologies, evidence table, timeline and ring subgraph per flagged account"

    return "n/a"


def describe_steps(plan: dict, events: list[dict] | None = None) -> list[dict]:
    """One entry per catalog tool the plan mentions, in the plan's own order,
    each carrying the Chose/Because/Returned triple the thread renders."""
    events = events or []
    by_tool: dict[str, dict] = {}
    for e in events:
        by_tool.setdefault(e["step"], e)

    filters = plan.get("filters") or {}
    out: list[dict] = []

    for step in plan.get("steps", []):
        tool = step["tool"]
        event = by_tool.get(tool)
        state = event["state"] if event else "pending"
        out.append({
            "tool": tool,
            "name": step_label(tool, filters),
            "state": state,
            "skipped": False,
            "skip_reason": None,
            "output": (event or {}).get("summary"),
            "elapsed_s": (event or {}).get("elapsed_s"),
            "chose": _chose(tool, plan),
            "because": step.get("reason", ""),
            "returned": (event or {}).get("summary") or "n/a",
        })

    for step in plan.get("skipped", []):
        tool = step["tool"]
        out.append({
            "tool": tool,
            "name": step_label(tool, filters),
            "state": "skipped",
            "skipped": True,
            "skip_reason": step.get("reason", ""),
            "output": None,
            "elapsed_s": None,
            "chose": "skipped — this tool never ran",
            "because": step.get("reason", ""),
            "returned": "nothing ran; no compute spent",
        })

    return out


def is_conceptual(plan: dict) -> bool:
    """True when the planner decided the question needs no data work at all
    (every tool skipped) — e.g. "what is structuring?". Distinct from a run
    that executed and found nothing: no threshold was ever evaluated, so
    claiming "no accounts met the thresholds" would be false."""
    return not plan.get("steps")


def prose_plan(plan: dict) -> str:
    """The sentence shown as soon as the plan exists, before any result —
    what was scoped and what was deliberately left out."""
    filters = plan.get("filters") or {}
    ran = [s["tool"] for s in plan.get("steps", [])]
    skipped = [s["tool"] for s in plan.get("skipped", [])]

    if is_conceptual(plan):
        return (
            "That's a question about how detection works rather than a query over the data, "
            "so I didn't run any analysis. No transactions were scanned and no thresholds "
            "were evaluated."
        )

    scope_bits = []
    if filters.get("accounts"):
        scope_bits.append(f"scoped the data to {', '.join(str(a) for a in filters['accounts'])}")
    elif filters.get("date_from") or filters.get("date_to"):
        scope_bits.append(f"scoped the data to {filters.get('date_from') or 'the start'} "
                          f"through {filters.get('date_to') or 'the end'}")
    elif filters.get("window_days"):
        scope_bits.append(f"scoped the data to the last {filters['window_days']} days")
    elif "filter_data" in ran:
        scope_bits.append("worked across the full sample")
    if filters.get("min_amount"):
        scope_bits.append(f"kept transactions at or above ${filters['min_amount']:,.0f}")

    detection = [PROSE_NOUNS[t] for t in ran if t in PROSE_NOUNS]
    if detection:
        scope_bits.append("ran " + _join(detection))

    sentence = "I " + _join(scope_bits) + "."

    notable = [PROSE_NOUNS[t] for t in skipped if t in PROSE_NOUNS]
    if notable:
        sentence += f" I skipped {_join(notable)}: {_skip_reason(plan, skipped)}"
    return sentence


def _skip_reason(plan: dict, skipped: list[str]) -> str:
    for step in plan.get("skipped", []):
        if step["tool"] in ("graph_analysis", "anomaly_model") and step.get("reason"):
            reason = step["reason"].strip()
            return reason[0].lower() + reason[1:] if reason else "not needed for this question."
    return "not needed for this question."


def explain_typologies() -> list[dict]:
    """Plain-language definition of every typology the system can detect,
    with the ACTUAL threshold each one uses. Assembled from the rule
    modules' own constants so the explanation can never drift from the
    code — if a threshold changes, this text changes with it."""
    return [
        {
            "name": "STRUCTURING_HIGH",
            "what": "Deliberately breaking a large sum into deposits that each stay under the "
                    f"${NEAR_THRESHOLD_HIGH:,.0f} reporting threshold, then moving the pooled money on.",
            "rule": f"{STRUCTURING_HIGH_MIN_COUNT}+ deposits between ${STRUCTURING_HIGH_LOW:,.0f} and "
                    f"${NEAR_THRESHOLD_HIGH:,.0f} into one account within {STRUCTURING_WINDOW_DAYS} days, "
                    f"AND at least {STRUCTURING_CONSOLIDATION_RATIO:.0%} of it sent back out within "
                    f"{STRUCTURING_CONSOLIDATION_WINDOW_DAYS} days.",
            "why": "The onward transfer is what separates laundering from an ordinary cash business: "
                   "a shop banks its takings and leaves them; a mule account gathers and forwards.",
        },
        {
            "name": "STRUCTURING_MEDIUM",
            "what": "The same sub-threshold deposit pattern, but without a confirmed onward transfer.",
            "rule": f"{STRUCTURING_MEDIUM_MIN_COUNT}+ transactions between ${STRUCTURING_MEDIUM_LOW:,.0f} and "
                    f"${NEAR_THRESHOLD_HIGH:,.0f} within {STRUCTURING_WINDOW_DAYS} days, either side of the account.",
            "why": "A weaker indicator on its own, since plenty of legitimate businesses look like this, "
                   "so it can put an account in front of an analyst but never escalates it by itself.",
        },
        {
            "name": "RAPID_MOVEMENT",
            "what": "Money arriving from several sources and leaving again almost immediately: a pass-through "
                    "or funnel account.",
            "rule": f"{RAPID_MOVEMENT_RATIO:.0%}+ of inbound funds sent out within 48 hours, on at least "
                    f"${RAPID_MOVEMENT_MIN_INBOUND:,.0f} gathered from {RAPID_MOVEMENT_MIN_SOURCES}+ distinct senders.",
            "why": "Requiring more than one sender is what distinguishes a funnel from ordinary "
                   "two-party settlement, where fast in-and-out is normal.",
        },
        {
            "name": "VELOCITY",
            "what": "A sudden burst of activity far outside what this account normally does.",
            "rule": f"Peak transactions-per-hour more than {VELOCITY_SIGMA:.0f} standard deviations above the "
                    f"account's own baseline, once it has {MIN_HISTORY_FOR_BASELINE}+ prior transactions.",
            "why": "The history minimum matters: a standard deviation from three transactions is noise, "
                   "not a baseline.",
        },
        {
            "name": "HIGH_RISK_AMOUNT",
            "what": "A single transaction wildly out of character for the account.",
            "rule": f"One amount more than {HIGH_RISK_AMOUNT_SIGMA:.0f} standard deviations above the account's own "
                    f"history, with {MIN_HISTORY_FOR_BASELINE}+ prior transactions.",
            "why": "Same reasoning as velocity: the account is compared against itself, not the population.",
        },
        {
            "name": "FAN_IN_RING",
            "what": "Many accounts feeding one collector account, which then forwards the pooled money on: "
                    "the classic smurfing ring.",
            "rule": f"{FAN_IN_MIN_SENDERS}+ distinct senders into one account within {FAN_IN_WINDOW_DAYS} days, "
                    f"with {FAN_IN_CONSOLIDATION_RATIO:.0%}+ of the gathered total moving onward.",
            "why": "Found on the transaction graph rather than in any single account's numbers, which is why "
                   "it corroborates the rules rather than repeating them.",
        },
        {
            "name": "CYCLE",
            "what": "Money that travels through several accounts and returns near where it started: layering "
                    "to obscure its origin.",
            "rule": f"A closed loop of 3 to {CYCLE_MAX_HOPS} hops on the transfer graph.",
            "why": "Two-party back-and-forth is excluded deliberately, since that's just two people who both pay "
                   "each other, which is extremely common and not laundering.",
        },
    ]


def prose_results(results: list[dict], cases: list[dict],
                  profile: dict | None = None, aggregation: dict | None = None) -> str:
    """The sentence shown once the run finishes — what was actually found.
    Counts come straight from the result set; nothing is estimated."""
    # A question can be answered by a count or a profile rather than by risk
    # flags. Reporting "nothing met the thresholds" when the answer was in
    # fact computed would be false, so those answers are stated first.
    if aggregation is not None:
        c = aggregation.get("criteria") or {}
        bits = []
        if c.get("min_count"):
            bits.append(f"{c['min_count']}+ transactions")
        if c.get("max_amount"):
            bits.append(f"under ${c['max_amount']:,.0f}")
        if c.get("min_amount"):
            bits.append(f"at or above ${c['min_amount']:,.0f}")
        criteria = " ".join(bits) if bits else "the requested criteria"
        n = aggregation.get("matched", 0)
        sentence = f"{n:,} account{'s' if n != 1 else ''} match {criteria}."
        rows = aggregation.get("rows") or []
        if rows:
            top = rows[0]
            sentence += (f" The highest is {top['account_id']} with {top['count']:,} such transactions"
                         f" totalling ${top['total_amount']:,.2f}.")
        if aggregation.get("truncated"):
            sentence += f" The table shows the top {len(rows):,} by count."
        return sentence

    if profile is not None and not results:
        return (
            f"{profile['n_txns']:,} transactions across {profile['n_accounts']:,} accounts, "
            f"spanning {profile['date_range'][0][:10]} to {profile['date_range'][1][:10]}, "
            f"totalling ${profile['total_volume']:,.2f} with a median transaction of "
            f"${profile['median_amount']:,.2f}."
        )

    if not results:
        return "No accounts met the detection thresholds for this question."

    tiers = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in results:
        tiers[r.get("risk_level", "LOW")] = tiers.get(r.get("risk_level", "LOW"), 0) + 1

    typologies: dict[str, int] = {}
    for r in results:
        for t in list(r.get("rules_fired", [])) + list(r.get("graph_fired", [])):
            typologies[t] = typologies.get(t, 0) + 1

    parts = [f"{len(results):,} account{'s' if len(results) != 1 else ''} surfaced"]
    tier_bits = [f"{n:,} {name.lower()}" for name, n in tiers.items() if n]
    if tier_bits:
        parts.append(_join(tier_bits))

    sentence = ", ".join(parts) + "."

    if typologies:
        top = sorted(typologies.items(), key=lambda kv: -kv[1])[:3]
        sentence += " Most common signals: " + _join(
            [f"{name.replace('_', ' ').lower()} ({n:,})" for name, n in top]
        ) + "."

    high = [c for c in cases if c.get("risk_level") == "HIGH"]
    if high:
        sentence += (
            f" {len(high):,} case{'s' if len(high) != 1 else ''} reached the report tier, "
            "where a named rule was corroborated by a second detection method."
        )
    return sentence


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def explain_case_plainly(case: dict) -> str:
    """Deterministic plain-language explanation of one case, built only from
    that case's own figures. This is the fallback when Gemini is unavailable
    and it is also the floor on quality: it can never invent a threshold or
    an amount, so a missing key degrades the prose, never the truth."""
    typ = [t.replace("_", " ").lower() for t in case.get("typologies", [])]
    inbound = [t for t in case.get("timeline", []) if t.get("direction") == "in"]
    outbound = [t for t in case.get("timeline", []) if t.get("direction") == "out"]
    in_sum = sum(float(t.get("amount", 0)) for t in inbound)
    out_sum = sum(float(t.get("amount", 0)) for t in outbound)
    senders = len({t.get("counterparty") for t in inbound})

    parts = [
        f"Account {case.get('account_id')} was flagged as {case.get('risk_level', 'unknown').lower()} risk"
        + (f" for {_join(typ)}." if typ else ".")
    ]
    if inbound:
        parts.append(
            f"It received {len(inbound)} payment{'s' if len(inbound) != 1 else ''} "
            f"totalling ${in_sum:,.2f}"
            + (f" from {senders} different account{'s' if senders != 1 else ''}." if senders else ".")
        )
    if outbound:
        parts.append(f"It then sent ${out_sum:,.2f} back out across {len(outbound)} "
                     f"payment{'s' if len(outbound) != 1 else ''}.")
    reasons = [str(e.get("reason")) for e in case.get("evidence", []) if e.get("reason")]
    if reasons:
        parts.append("In plain terms: " + reasons[0][0].lower() + reasons[0][1:] + ".")
    parts.append(f"The recommended next step is to {case.get('recommended_action', 'monitor')}.")
    return " ".join(parts)
