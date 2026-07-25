"""Unit tests for the planner's resilience path (cache, fallback) — kept
network-independent by monkeypatching the LLM call. Live-plan correctness
for the 3 canonical queries (e.g. "query 2 must skip anomaly_model and
graph_analysis") is verified by evals/run.py against the real API, not here.
"""

import importlib
import json

from agent import planner


def test_fallback_plan_covers_every_tool_exactly_once():
    plan = planner._fallback_plan("test reason")
    covered = [s["tool"] for s in plan["steps"]] + [s["tool"] for s in plan["skipped"]]
    assert sorted(covered) == sorted(planner.TOOL_NAMES)
    assert len(covered) == len(set(covered)), "no tool should appear in both steps and skipped"
    assert plan["_offline_fallback"] is True
    assert plan["clarification_needed"] is None


def test_plan_query_falls_back_to_heuristic_when_llm_fails_and_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "CACHE_DIR", tmp_path)  # guarantee a cold cache

    def _raise(query):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(planner, "_call_llm", _raise)

    plan = planner.plan_query("a query that has never been asked before, unique-xyz-123")
    assert plan["_offline_fallback"] is True
    assert "simulated network failure" in plan["_fallback_reason"]


def test_plan_query_serves_cached_plan_when_llm_fails(monkeypatch, tmp_path):
    cache_path = tmp_path / "precached.json"
    seeded_plan = {
        "intent": "detect_structuring",
        "filters": {"window_days": 30, "min_amount": None, "accounts": None},
        "typologies": ["structuring"],
        "steps": [{"tool": "filter_data", "params": {}, "reason": "x"}],
        "skipped": [],
        "clarification_needed": None,
    }
    cache_path.write_text(json.dumps(seeded_plan))
    monkeypatch.setattr(planner, "_cache_path", lambda q: cache_path)

    def _raise(q):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(planner, "_call_llm", _raise)

    plan = planner.plan_query("cached test query")
    assert plan["_served_from_cache"] is True
    assert plan["intent"] == "detect_structuring"


def test_plan_schema_names_match_actual_tool_catalog():
    """The tool names in the planner's schema/prompt must match the real
    backend tool modules — a renamed tool here would silently desync the
    planner from what the executor can actually run."""
    expected_modules = {
        "filter_data": "tools.filter_data",
        "profile_data": "tools.profile_data",
        "feature_engine": "tools.feature_engine",
        "rules_engine": "tools.rules_engine",
        "anomaly_model": "tools.anomaly_model",
        "graph_analysis": "tools.graph_analysis",
        "risk_scorer": "tools.risk_scorer",
    }
    assert set(planner.TOOL_NAMES) == set(expected_modules)
    for module_path in expected_modules.values():
        importlib.import_module(module_path)  # raises ImportError if missing
