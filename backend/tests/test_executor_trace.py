"""executor — trace-event contract: ordering, the skipped/running state
machine, per-event summaries, mid-plan failure handling, and trace
isolation between separate runs.

Note on "pending": the backend never emits an explicit pending event — a
step only enters `events` the moment it starts (created already in state
"running", then mutated to "done"/"error" in place). A step not yet
reached simply hasn't appeared in the list yet. The frontend's "pending ->
running -> done" rendering (CLAUDE.md's trace-ledger spec) infers pending
from a step's absence, given the plan tells it what's coming. The first
test below proves that inference is valid: while step N is running, no
step after it has appeared in `events` yet.
"""

from __future__ import annotations

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


def test_steps_not_yet_reached_are_absent_from_events_mid_run(monkeypatch):
    """While rules_engine is running, feature_engine must already be "done"
    and risk_scorer must not have appeared in `events` at all yet — proving
    a frontend can validly render "pending" for anything absent."""
    snapshot = {}
    real_rules_engine = executor_module.rules_engine

    def _spy(df, features, typologies=None):
        snapshot["states_at_rules_engine_start"] = [
            (e["step"], e["state"]) for e in events_ref[0]
        ]
        return real_rules_engine(df, features, typologies=typologies)

    events_ref = [[]]
    monkeypatch.setattr(executor_module, "rules_engine", _spy)

    df = load_transactions()
    events: list[dict] = []
    events_ref[0] = events
    run_plan(df, STRUCTURING_PLAN, events)

    states = dict(snapshot["states_at_rules_engine_start"])
    assert states["filter_data"] == "done"
    assert states["feature_engine"] == "done"
    assert "rules_engine" in states and states["rules_engine"] == "running"
    assert "risk_scorer" not in states, "a step not yet reached must not appear in events at all"


def test_skipped_tools_appear_with_reason_and_never_run():
    df = load_transactions()
    events: list[dict] = []
    run_plan(df, STRUCTURING_PLAN, events)

    skipped_events = {e["step"]: e for e in events if e["state"] == "skipped"}
    assert set(skipped_events) == {"profile_data", "anomaly_model", "graph_analysis"}
    for step, event in skipped_events.items():
        assert event["reason"], f"{step} skipped without a reason"
        assert event["summary"] is None
    assert not any(e["state"] == "running" for e in events if e["step"] in skipped_events)


def test_every_completed_step_has_a_one_line_summary_and_timing():
    df = load_transactions()
    events: list[dict] = []
    run_plan(df, STRUCTURING_PLAN, events)

    done_events = [e for e in events if e["state"] == "done"]
    assert done_events
    for e in done_events:
        assert isinstance(e["summary"], str) and e["summary"]
        assert isinstance(e["elapsed_s"], float)
        assert e["elapsed_s"] >= 0


def test_mid_plan_failure_is_captured_per_step_and_does_not_raise(monkeypatch):
    def _boom(df, features, typologies=None):
        raise RuntimeError("simulated rules_engine failure")

    monkeypatch.setattr(executor_module, "rules_engine", _boom)

    df = load_transactions()
    events: list[dict] = []
    outcome = run_plan(df, STRUCTURING_PLAN, events)  # must not raise

    failed = next(e for e in events if e["step"] == "rules_engine")
    assert failed["state"] == "error"
    assert "simulated rules_engine failure" in failed["summary"]

    # the run continues past the failure rather than hanging — risk_scorer
    # still gets an event (even though it has nothing to score, since
    # rule_flags stayed at its initial empty state)
    risk_scorer_event = next(e for e in events if e["step"] == "risk_scorer")
    assert risk_scorer_event["state"] == "done"
    assert outcome["results"] == []


def test_two_separate_runs_do_not_share_or_leak_trace_state():
    df = load_transactions()
    events_a: list[dict] = []
    events_b: list[dict] = []

    run_plan(df, STRUCTURING_PLAN, events_a)
    # a plan with a DIFFERENT skip set, to make cross-contamination obvious
    other_plan = {
        **STRUCTURING_PLAN,
        "steps": [{"tool": "filter_data", "params": {}, "reason": "x"}],
        "skipped": [
            {"tool": "profile_data", "reason": "x"}, {"tool": "feature_engine", "reason": "x"},
            {"tool": "rules_engine", "reason": "x"}, {"tool": "anomaly_model", "reason": "x"},
            {"tool": "graph_analysis", "reason": "x"}, {"tool": "risk_scorer", "reason": "x"},
        ],
    }
    run_plan(df, other_plan, events_b)

    assert events_a is not events_b
    assert {e["step"] for e in events_a if e["state"] != "skipped"} == {
        "filter_data", "feature_engine", "rules_engine", "risk_scorer", "case_builder",
    }
    assert {e["step"] for e in events_b if e["state"] != "skipped"} == {"filter_data", "case_builder"}


def test_case_builder_finalization_failure_propagates_unlike_per_step_tool_failures(monkeypatch):
    """Asymmetry worth documenting, not silently papering over: each
    detection tool's own step is individually try/excepted (a failure
    becomes an "error" trace event and the run continues — proven above),
    but the final case_builder assembly block is NOT wrapped the same way.
    A failure there propagates out of run_plan entirely, which is exactly
    what lets app.main._execute set the top-level trace status to "error"
    at all — that path only exists via this one unguarded block."""
    monkeypatch.setattr(executor_module, "build_indexes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    df = load_transactions()
    events: list[dict] = []
    try:
        run_plan(df, STRUCTURING_PLAN, events)
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "unlike per-step tool failures, a case_builder finalization failure must propagate"
