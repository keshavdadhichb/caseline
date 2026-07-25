"""executor — runs a plan's steps against the real tools in order, recording
a trace event per step (pending -> running -> done, with a one-line result
summary). Filters are taken authoritatively from plan["filters"] (not each
step's own "params", which the LLM fills in mainly for trace-panel display)
so execution semantics never depend on the LLM echoing them consistently.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

import pandas as pd

from tools.anomaly_model import anomaly_model
from tools.case_builder import case_builder
from tools.feature_engine import feature_engine
from tools.filter_data import filter_data
from tools.graph_analysis import graph_analysis
from tools.profile_data import profile_data
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine
from tools.sar_drafter import _draft_template, draft_sar

MAX_LIVE_SAR_DRAFTS = 10  # protects an adversarial/broad query from spiraling into minutes of LLM calls

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

    # Finalization is automatic, not a planned/skippable step — every scored
    # account gets a case file, and only HIGH-risk cases (the "report"
    # escalation tier) get an LLM-drafted SAR narrative. It still gets its
    # own trace events so the panel keeps showing progress instead of going
    # quiet for the ~15-20s a batch of live drafts can take.
    events.append({"step": "case_builder", "state": "running", "summary": None, "reason": "assemble case files"})
    t0 = time.monotonic()
    cases = [
        case_builder(record, state["rule_flags"], state["graph_flags"], state["df"])
        for record in risk_records
    ]
    events[-1].update(state="done", summary=f"{len(cases)} case files assembled",
                       elapsed_s=round(time.monotonic() - t0, 3))

    high_cases = [c for c in cases if c.risk_level == "HIGH"]
    if high_cases:
        # SAR drafting is I/O-bound (one LLM call each) — a query that
        # surfaces a whole ring (e.g. an aggregator + its mules) can produce
        # a dozen HIGH cases at once, and drafting those sequentially would
        # multiply single-call latency by the case count. Parallelize, and
        # cap how many get a live draft — beyond the cap, cases still get
        # the (fast, free, always-available) template narrative rather than
        # letting an adversarial/broad query spiral into minutes of calls.
        events.append({"step": "sar_drafter", "state": "running", "summary": None,
                        "reason": f"draft SAR narratives for {len(high_cases)} HIGH case(s)"})
        t0 = time.monotonic()
        live_batch, template_batch = high_cases[:MAX_LIVE_SAR_DRAFTS], high_cases[MAX_LIVE_SAR_DRAFTS:]
        if live_batch:
            with ThreadPoolExecutor(max_workers=min(8, len(live_batch))) as pool:
                narratives = list(pool.map(draft_sar, live_batch))
            for case, narrative in zip(live_batch, narratives):
                case.narrative = narrative
        for case in template_batch:
            case.narrative = _draft_template(case)
        summary = f"{len(live_batch)} drafted live"
        if template_batch:
            summary += f", {len(template_batch)} via template (over the {MAX_LIVE_SAR_DRAFTS}-draft live cap)"
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
