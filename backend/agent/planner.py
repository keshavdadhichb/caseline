"""planner — one LLM call: user query + tool catalog -> strict JSON
execution plan. Uses forced tool-use (not output_config.format — structured
outputs support is not confirmed on claude-sonnet-4-6) so the response is
always schema-valid JSON, never free text to parse.

Live call first; every successful plan is cached to disk keyed by the
query so the same question always replays byte-identical and the demo
survives with wifi off (CLAUDE.md "Resilience & demo insurance"). On live
failure or a slow response (>LIVE_TIMEOUT_SECONDS), falls back to a cached
plan for this exact query if one exists, then to a generic heuristic plan
as a last resort — the system never simply errors out on a planning
failure.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
LIVE_TIMEOUT_SECONDS = 8.0
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "plans"

TOOL_CATALOG = [
    {
        "name": "filter_data",
        "description": "Date/account/amount/channel scoping. Almost always the first step.",
    },
    {
        "name": "profile_data",
        "description": (
            "Quick EDA summary (txn count, accounts, date range, volume, channel "
            "breakdown). ONLY for broad/overview queries — skip for targeted or "
            "single-entity queries."
        ),
    },
    {
        "name": "feature_engine",
        "description": (
            "Per-account rolling features: 7d/30d txn count & sum, amount z-score, "
            "velocity, % near the $10,000 threshold, rapid in->out ratio. Required "
            "by rules_engine, anomaly_model, and risk_scorer."
        ),
    },
    {
        "name": "rules_engine",
        "description": (
            "Named typology rules: structuring, velocity, rapid_movement, "
            "high_risk_amount. Use when the query targets one or more of these "
            "typologies, or asks a threshold/aggregation question directly "
            "answerable by rules alone."
        ),
    },
    {
        "name": "anomaly_model",
        "description": (
            "IsolationForest anomaly score per account vs. the full population "
            "baseline. Use for broad 'find anything suspicious' queries or when "
            "rules alone might miss something; skip for narrowly-scoped rule or "
            "threshold questions — it adds nothing there."
        ),
    },
    {
        "name": "graph_analysis",
        "description": (
            "networkx fan-in ring and short-cycle detection. ONLY when the query "
            "asks about a network/ring/multiple-accounts pattern, or investigates "
            "a specific entity's counterparties. Skip for simple threshold or "
            "aggregation queries."
        ),
    },
    {
        "name": "risk_scorer",
        "description": (
            "Combines rule hits + anomaly score + graph findings into a weighted "
            "LOW/MEDIUM/HIGH risk level. Run whenever any detection tool above "
            "would produce flags worth reviewing."
        ),
    },
]
TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]

SYSTEM_PROMPT = f"""You are Caseline's query planner for a bank AML compliance tool. \
Given a compliance analyst's natural-language question, decide which of the following \
tools are actually needed to answer it, and which can be skipped — and say why for each.

Tool catalog:
{json.dumps(TOOL_CATALOG, indent=2)}

Rules:
- filter_data almost always runs first if the query implies any date/amount/account scoping.
- Only include a tool in "steps" if THIS query specifically needs it. Skipping unnecessary \
tools (and saying why) is the entire point of this planner — a query like "which accounts \
made 10+ transactions under $10,000" needs filter_data + feature_engine + rules_engine and \
NOTHING else; anomaly_model and graph_analysis add nothing to a pure threshold aggregation \
and must be skipped.
- window_days is relative to the dataset's own latest transaction timestamp, not today's \
date — the data is historical (September 2022). Only set it when the query implies a time scope.
- If an account is named directly (e.g. "customer ID 4521"), put it in filters.accounts.
- If the query is genuinely ambiguous in a way that changes what you'd compute (a \
time-dependent question with no window given, or "suspicious" with no typology or entity \
at all), set clarification_needed to ONE short question instead of guessing.
- Every tool in the catalog must appear in EITHER "steps" or "skipped" — never omit one."""


def _plan_tool_schema() -> dict:
    return {
        "name": "build_execution_plan",
        "description": "Emit the query's execution plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "short snake_case label, e.g. detect_structuring, "
                        "aggregate_threshold, entity_lookup, detect_ring, overview"
                    ),
                },
                "filters": {
                    "type": "object",
                    "properties": {
                        "window_days": {"type": ["integer", "null"]},
                        "min_amount": {"type": ["number", "null"]},
                        "accounts": {"type": ["array", "null"], "items": {"type": "string"}},
                    },
                    "required": ["window_days", "min_amount", "accounts"],
                },
                "typologies": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["structuring", "velocity", "rapid_movement", "high_risk_amount"],
                    },
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": TOOL_NAMES},
                            "params": {"type": "object"},
                            "reason": {"type": "string"},
                        },
                        "required": ["tool", "params", "reason"],
                    },
                },
                "skipped": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": TOOL_NAMES},
                            "reason": {"type": "string"},
                        },
                        "required": ["tool", "reason"],
                    },
                },
                "clarification_needed": {"type": ["string", "null"]},
            },
            "required": ["intent", "filters", "typologies", "steps", "skipped", "clarification_needed"],
        },
    }


def _cache_path(full_query: str) -> Path:
    key = hashlib.sha256(full_query.strip().lower().encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}.json"


def _fallback_plan(reason: str) -> dict:
    """Deterministic, non-LLM plan used only when both a live call and the
    disk cache are unavailable — keeps the system answering something
    instead of erroring out."""
    return {
        "intent": "general_review",
        "filters": {"window_days": 30, "min_amount": None, "accounts": None},
        "typologies": ["structuring", "velocity", "rapid_movement", "high_risk_amount"],
        "steps": [
            {"tool": "filter_data", "params": {"window_days": 30},
             "reason": "default 30-day scope (planner unavailable — offline fallback)"},
            {"tool": "feature_engine", "params": {}, "reason": "required by rules_engine and risk_scorer"},
            {"tool": "rules_engine", "params": {}, "reason": "run all typologies since intent could not be classified"},
            {"tool": "anomaly_model", "params": {}, "reason": "catch anything the rules miss"},
            {"tool": "graph_analysis", "params": {}, "reason": "check for ring/network patterns"},
            {"tool": "risk_scorer", "params": {}, "reason": "combine all signals for review"},
        ],
        "skipped": [
            {"tool": "profile_data", "reason": "targeted detection already covers this"},
        ],
        "clarification_needed": None,
        "_offline_fallback": True,
        "_fallback_reason": reason,
    }


def _call_llm(query: str) -> dict:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[_plan_tool_schema()],
        tool_choice={"type": "tool", "name": "build_execution_plan"},
        messages=[{"role": "user", "content": query}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"planner: no tool_use block in response (stop_reason={response.stop_reason})")


def plan_query(query: str, clarification_answer: str | None = None) -> dict:
    """Returns the execution plan dict. Always succeeds — degrades through
    live call -> disk cache -> heuristic fallback."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    full_query = query if not clarification_answer else f"{query}\n\n(clarification: {clarification_answer})"
    cache_path = _cache_path(full_query)

    t0 = time.monotonic()
    try:
        plan = _call_llm(full_query)
        elapsed = time.monotonic() - t0
        if elapsed > LIVE_TIMEOUT_SECONDS and cache_path.exists():
            # slow but successful — still cache the fresh result for next time,
            # but this run serves the last-known-good cached plan instead
            cache_path.write_text(json.dumps(plan, indent=2))
            cached = json.loads(cache_path.read_text())
            cached["_served_from_cache"] = True
            cached["_cache_reason"] = f"live call took {elapsed:.1f}s (> {LIVE_TIMEOUT_SECONDS}s budget)"
            return cached
        cache_path.write_text(json.dumps(plan, indent=2))
        return plan
    except Exception as exc:  # noqa: BLE001 — any failure falls through to cache/fallback
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached["_served_from_cache"] = True
            cached["_cache_reason"] = str(exc)
            return cached
        return _fallback_plan(str(exc))
