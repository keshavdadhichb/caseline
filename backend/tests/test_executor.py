"""Unit tests for the executor — proves the plan->tool wiring is correct
and, specifically, regresses a real bug found during live testing: when
anomaly_model is skipped, risk_scorer must never cite "anomaly model" in
an explanation or contribute a nonzero anomaly_component. A naive
placeholder score of 0.0 run through normalize_anomaly_score's
population-percentile mapping fabricated a plausible-looking signal for a
tool that never ran (0.55 for a raw score that was never computed).
"""

import agent.executor as executor_module
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

# Same plan, but with "steps" in an adversarial order — observed live from
# the actual planner: risk_scorer listed BEFORE graph_analysis. Nothing in
# the JSON schema constrains step order, so the executor must not depend on it.
ENTITY_LOOKUP_PLAN_BAD_ORDER = {
    **ENTITY_LOOKUP_PLAN,
    "steps": [
        {"tool": "risk_scorer", "params": {}, "reason": "combine everything"},
        {"tool": "graph_analysis", "params": {}, "reason": "check for ring involvement"},
        {"tool": "anomaly_model", "params": {}, "reason": "deep dive on this entity"},
        {"tool": "rules_engine", "params": {}, "reason": "check known typologies"},
        {"tool": "feature_engine", "params": {}, "reason": "needed downstream"},
        {"tool": "filter_data", "params": {}, "reason": "scope to account 4521"},
    ],
}


def test_step_order_is_canonical_regardless_of_llm_ordering(monkeypatch):
    """Regression test for a real bug: a live plan once listed risk_scorer
    before graph_analysis, which ran risk_scorer against an empty
    graph_flags and silently dropped the FAN_IN_RING signal from every
    score (4521 scored 0.65 instead of 1.0, with no "graph" citation).
    The executor must reorder to a safe dependency order internally."""
    monkeypatch.setattr(executor_module, "draft_sar", lambda case: f"stub narrative for {case.account_id}")

    df = load_transactions()
    events: list[dict] = []
    outcome = run_plan(df, ENTITY_LOOKUP_PLAN_BAD_ORDER, events)

    by_account = {r["account_id"]: r for r in outcome["results"]}
    record = by_account["4521"]
    assert record["risk_level"] == "HIGH"
    assert record["score"] == 1.0
    assert "graph" in record["explanation"].lower(), "graph signal was dropped by adversarial step order"

    executed_order = [
        e["step"] for e in events
        if e["state"] != "skipped" and e["step"] in executor_module.CANONICAL_ORDER
    ]
    assert executed_order == [t for t in executor_module.CANONICAL_ORDER if t in executed_order], (
        "execution order must follow the canonical dependency order, not the plan's listed order"
    )


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


def test_entity_lookup_plan_resolves_ring_aggregator_end_to_end(monkeypatch):
    # draft_sar makes a real LLM call per HIGH case (the ring produces 10) —
    # monkeypatch it so this stays a fast, network-independent unit test.
    # Live narrative quality is checked by evals/run.py and the manual e2e script.
    monkeypatch.setattr(executor_module, "draft_sar", lambda case: f"stub narrative for {case.account_id}")

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
    assert tool_events["case_builder"] == "done"
    assert tool_events["sar_drafter"] == "done"

    ring_case = next(c for c in outcome["cases"] if c["account_id"] == "4521")
    assert ring_case["narrative"] == "stub narrative for 4521"


def test_sar_draft_cap_falls_back_to_template_beyond_cap(monkeypatch):
    """The ring produces 10 HIGH cases (aggregator + 9 mules). Lower the
    live-draft cap to 3 to deterministically exercise the overflow path
    without needing more than 10 real HIGH accounts in the fixture data."""
    monkeypatch.setattr(executor_module, "MAX_LIVE_SAR_DRAFTS", 3)
    monkeypatch.setattr(executor_module, "draft_sar", lambda case: f"LIVE:{case.account_id}")

    df = load_transactions()
    events: list[dict] = []
    outcome = run_plan(df, ENTITY_LOOKUP_PLAN, events)

    high_cases = [c for c in outcome["cases"] if c["risk_level"] == "HIGH"]
    assert len(high_cases) == 10
    live = [c for c in high_cases if c["narrative"].startswith("LIVE:")]
    templated = [c for c in high_cases if not c["narrative"].startswith("LIVE:")]
    assert len(live) == 3
    assert len(templated) == 7
    for c in templated:
        assert "template" in c["narrative"].lower()

    sar_event = next(e for e in events if e["step"] == "sar_drafter")
    assert sar_event["state"] == "done"
    assert "3 drafted live" in sar_event["summary"]
    assert "7 via template" in sar_event["summary"]


def test_run_plan_reports_a_summary_per_executed_step():
    df = load_transactions()
    events: list[dict] = []
    run_plan(df, STRUCTURING_PLAN, events)

    done_events = [e for e in events if e["state"] == "done"]
    # filter_data, feature_engine, rules_engine, risk_scorer, + the automatic
    # case_builder finalization step (no HIGH accounts here, so sar_drafter
    # never runs and adds no event)
    assert len(done_events) == 5
    for e in done_events:
        assert e["summary"], f"{e['step']} produced no summary"
        assert isinstance(e["elapsed_s"], float)
