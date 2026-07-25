"""Unit tests for the executor — proves the plan->tool wiring is correct
and, specifically, regresses a real bug found during live testing: when
anomaly_model is skipped, risk_scorer must never cite "anomaly model" in
an explanation or contribute a nonzero anomaly_component. A naive
placeholder score of 0.0 run through normalize_anomaly_score's
population-percentile mapping fabricated a plausible-looking signal for a
tool that never ran (0.55 for a raw score that was never computed).
"""

from agent.executor import run_plan
from app.data_loader import load_transactions

STRUCTURING_PLAN = {
    "intent": "detect_structuring",
    "filters": {"window_days": 30, "min_amount": None, "accounts": None},
    "typologies": ["structuring"],
    "steps": [
        {"tool": "filter_data", "params": {}, "reason": "scope to 30d"},
        {"tool": "feature_engine", "params": {}, "reason": "needed by rules_engine"},
        {"tool": "rules_engine", "params": {}, "reason": "structuring rule"},
        {"tool": "risk_scorer", "params": {}, "reason": "score the flags"},
    ],
    "skipped": [
        {"tool": "profile_data", "reason": "targeted query"},
        {"tool": "anomaly_model", "reason": "rules alone answer this"},
        {"tool": "graph_analysis", "reason": "not a network question"},
    ],
    "clarification_needed": None,
}

ENTITY_LOOKUP_PLAN = {
    "intent": "entity_lookup",
    "filters": {"window_days": None, "min_amount": None, "accounts": ["4521"]},
    "typologies": [],
    "steps": [
        {"tool": "filter_data", "params": {}, "reason": "scope to account 4521"},
        {"tool": "feature_engine", "params": {}, "reason": "needed downstream"},
        {"tool": "rules_engine", "params": {}, "reason": "check known typologies"},
        {"tool": "anomaly_model", "params": {}, "reason": "deep dive on this entity"},
        {"tool": "graph_analysis", "params": {}, "reason": "check for ring involvement"},
        {"tool": "risk_scorer", "params": {}, "reason": "combine everything"},
    ],
    "skipped": [{"tool": "profile_data", "reason": "single-entity query"}],
    "clarification_needed": None,
}


def test_skipped_anomaly_model_never_appears_in_explanations():
    df = load_transactions()
    events: list[dict] = []
    outcome = run_plan(df, STRUCTURING_PLAN, events)

    assert outcome["results"], "expected structuring to flag some accounts"
    for r in outcome["results"]:
        assert "anomaly" not in r["explanation"].lower(), (
            f"anomaly_model was skipped but its explanation cites it: {r['explanation']}"
        )
        assert r["anomaly_component"] == 0.0

    tool_events = {e["step"]: e["state"] for e in events}
    assert tool_events["anomaly_model"] == "skipped"
    assert tool_events["filter_data"] == "done"
    assert tool_events["risk_scorer"] == "done"


def test_entity_lookup_plan_resolves_ring_aggregator_end_to_end():
    df = load_transactions()
    events: list[dict] = []
    outcome = run_plan(df, ENTITY_LOOKUP_PLAN, events)

    by_account = {r["account_id"]: r for r in outcome["results"]}
    assert "4521" in by_account
    record = by_account["4521"]
    assert record["risk_level"] == "HIGH"
    assert "anomaly" in record["explanation"].lower(), "anomaly_model DID run here — must be cited"
    assert "rules" in record["explanation"].lower()
    assert "graph" in record["explanation"].lower()

    tool_events = {e["step"]: e["state"] for e in events}
    assert tool_events["anomaly_model"] == "done"
    assert tool_events["graph_analysis"] == "done"


def test_run_plan_reports_a_summary_per_executed_step():
    df = load_transactions()
    events: list[dict] = []
    run_plan(df, STRUCTURING_PLAN, events)

    done_events = [e for e in events if e["state"] == "done"]
    assert len(done_events) == 4  # filter_data, feature_engine, rules_engine, risk_scorer
    for e in done_events:
        assert e["summary"], f"{e['step']} produced no summary"
        assert isinstance(e["elapsed_s"], float)
