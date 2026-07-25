"""executor — runs a plan's steps against the real tools in order, recording
a trace event per step (pending -> running -> done, with a one-line result
summary). Filters are taken authoritatively from plan["filters"] (not each
step's own "params", which the LLM fills in mainly for trace-panel display)
so execution semantics never depend on the LLM echoing them consistently.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

import pandas as pd

from tools.anomaly_model import anomaly_model
from tools.case_builder import build_indexes, case_builder
from tools.feature_engine import feature_engine
from tools.filter_data import filter_data
from tools.graph_analysis import graph_analysis
from tools.profile_data import profile_data
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine

# Dependency-respecting execution order. The planner's JSON lists "steps" in
# whatever order the LLM happened to emit them — nothing in the schema
# guarantees risk_scorer comes after graph_analysis, and it doesn't always:
# observed live, a plan listed risk_scorer BEFORE graph_analysis, which
# silently ran risk_scorer against an empty graph_flags and dropped the
# FAN_IN_RING signal from every score. Executing steps in the LLM's literal
# order is therefore unsafe; always execute in this canonical order instead
# (still using each step's own "reason" text for trace/UI display).
CANONICAL_ORDER = [
    "filter_data", "profile_data", "feature_engine",
    "rules_engine", "anomaly_model", "graph_analysis", "risk_scorer",
]

# Max full case files built per query. risk_records is sorted by score, so
# this is the top-N most suspicious accounts — an analyst never reviews more,
# and every scored account still appears in the lightweight results list.
CASE_BUILD_LIMIT = 300


def run_plan(df: pd.DataFrame, plan: dict, events: list[dict]) -> dict:
    """Executes `plan` against `df`, appending to `events` as it goes (the
    caller owns the trace's status — this only produces events + a result).
    Returns {"results": [...], "risk_records": [...]} for case_builder to
    consume next."""
    state: dict[str, Any] = {
        "df": df,
        "profile": None,
        "features": None,
        "rule_flags": [],
        "graph_flags": [],
        "scored_features": None,
        "risk_records": [],
    }

    steps_by_tool = {s["tool"]: s for s in plan.get("steps", [])}
    ordered_steps = [steps_by_tool[t] for t in CANONICAL_ORDER if t in steps_by_tool]
    # defensive: a tool outside the known catalog (shouldn't happen given the
    # schema's enum constraint) still runs, just last and in its own order
    ordered_steps += [s for s in plan.get("steps", []) if s["tool"] not in CANONICAL_ORDER]

    for step in ordered_steps:
        tool = step["tool"]
        event = {"step": tool, "state": "running", "summary": None, "reason": step.get("reason", "")}
        events.append(event)
        t0 = time.monotonic()
        try:
            event["summary"] = _run_step(tool, plan, state)
            event["state"] = "done"
        except Exception as exc:  # noqa: BLE001 — one bad step shouldn't crash the whole run
            event["state"] = "error"
            event["summary"] = f"error: {exc}"
        event["elapsed_s"] = round(time.monotonic() - t0, 3)

    for step in plan.get("skipped", []):
        events.append({
            "step": step["tool"], "state": "skipped", "summary": None, "reason": step.get("reason", ""),
        })

    risk_records = state["risk_records"]

    # Finalization: assemble case files for the highest-risk accounts. Two
    # bounds keep this fast regardless of how broad the query is:
    #   1. Only the top CASE_BUILD_LIMIT accounts by score get a full case
    #      file (timeline, ring subgraph, evidence table). A broad query can
    #      score 15k+ accounts and no analyst reviews that many — the
    #      lightweight `results` list still carries every scored account, so
    #      nothing is hidden, it just isn't pre-assembled into a heavy case.
    #   2. build_indexes pre-groups transactions + flags by account ONCE so
    #      each case file is O(that account's activity), not a full-frame
    #      scan (the earlier per-case scan made a broad query take minutes).
    # SAR narratives are drafted lazily on case-open (GET /api/case/{id}),
    # never here.
    events.append({"step": "case_builder", "state": "running", "summary": None, "reason": "assemble case files"})
    t0 = time.monotonic()
    idx = build_indexes(state["df"], state["rule_flags"], state["graph_flags"])
    top_records = risk_records[:CASE_BUILD_LIMIT]  # risk_records is already sorted by score desc
    cases = [case_builder(record, state["df"], idx) for record in top_records]
    summary = f"{len(cases)} case files assembled"
    if len(risk_records) > CASE_BUILD_LIMIT:
        summary += f" (top {CASE_BUILD_LIMIT} of {len(risk_records):,} scored accounts)"
    events[-1].update(state="done", summary=summary, elapsed_s=round(time.monotonic() - t0, 3))

    return {
        "results": [asdict(r) for r in risk_records],
        "cases": [asdict(c) for c in cases],
    }


def _run_step(tool: str, plan: dict, state: dict) -> str:
    if tool == "filter_data":
        filters = plan.get("filters") or {}
        state["df"] = filter_data(
            state["df"],
            window_days=filters.get("window_days"),
            min_amount=filters.get("min_amount"),
            accounts=filters.get("accounts"),
        )
        return f"{len(state['df']):,} transactions in scope"

    if tool == "profile_data":
        state["profile"] = profile_data(state["df"])
        p = state["profile"]
        return f"{p['n_txns']:,} txns, {p['n_accounts']:,} accounts, ${p['total_volume']:,.0f} volume"

    if tool == "feature_engine":
        state["features"] = feature_engine(state["df"])
        return f"{len(state['df']):,} txns -> {len(state['features']):,} account-windows"

    if tool == "rules_engine":
        if state["features"] is None:
            state["features"] = feature_engine(state["df"])
        typologies = plan.get("typologies") or None
        state["rule_flags"] = rules_engine(state["df"], state["features"], typologies=typologies)
        return f"{len(state['rule_flags'])} rule flags"

    if tool == "anomaly_model":
        if state["features"] is None:
            state["features"] = feature_engine(state["df"])
        state["scored_features"] = anomaly_model(state["features"])
        return f"scored {len(state['scored_features']):,} accounts against population baseline"

    if tool == "graph_analysis":
        state["graph_flags"] = graph_analysis(state["df"])
        return f"{len(state['graph_flags'])} graph flags"

    if tool == "risk_scorer":
        # scored_features stays None when anomaly_model was skipped for this
        # query — risk_scorer treats that as "no anomaly signal", not as a
        # score of exactly 0 on the model's own scale (those aren't the same
        # thing; see the docstring on risk_scorer for why that distinction matters).
        state["risk_records"] = risk_scorer(
            state["rule_flags"], state["graph_flags"], state["scored_features"]
        )
        return f"{len(state['risk_records'])} accounts scored"

    raise ValueError(f"unknown tool: {tool}")
