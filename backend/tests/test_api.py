"""API-level tests using FastAPI's TestClient — exercise the HTTP contract
(submit -> poll trace -> fetch results -> open case) without a running
server or live network. The planner and SAR drafter are monkeypatched so
these are fast and deterministic; live behavior is covered by evals/run.py.
"""

import app.main as main
from fastapi.testclient import TestClient

ENTITY_PLAN = {
    "intent": "entity_lookup",
    "filters": {"window_days": None, "min_amount": None, "accounts": ["4521"]},
    "typologies": [],
    "steps": [
        {"tool": "filter_data", "params": {}, "reason": "scope to 4521"},
        {"tool": "feature_engine", "params": {}, "reason": "downstream"},
        {"tool": "rules_engine", "params": {}, "reason": "typologies"},
        {"tool": "anomaly_model", "params": {}, "reason": "entity deep dive"},
        {"tool": "graph_analysis", "params": {}, "reason": "ring check"},
        {"tool": "risk_scorer", "params": {}, "reason": "combine"},
    ],
    "skipped": [{"tool": "profile_data", "reason": "single entity"}],
    "clarification_needed": None,
}


def test_full_flow_submit_poll_results_and_lazy_sar(monkeypatch):
    monkeypatch.setattr(main, "plan_query", lambda q, c=None: ENTITY_PLAN)
    monkeypatch.setattr(main, "draft_sar", lambda case: f"LAZY SAR for {case.account_id}")

    with TestClient(main.app) as client:  # `with` triggers lifespan warmup
        submit = client.post("/api/query", json={"query": "Is customer ID 4521 suspicious?"}).json()
        trace_id = submit["trace_id"]
        assert submit["clarification_needed"] is None
        assert "risk_scorer" in [s["tool"] for s in submit["plan"]["steps"]]

        # TestClient runs BackgroundTasks synchronously, so the run is already
        # done by the time the POST returns
        trace = client.get(f"/api/query/{trace_id}/trace").json()
        assert trace["status"] == "done"

        results = client.get(f"/api/query/{trace_id}/results").json()
        ring = next(r for r in results["results"] if r["account_id"] == "4521")
        assert ring["risk_level"] == "HIGH"

        # results carry the case with NO narrative yet (lazy)
        ring_case = next(c for c in results["cases"] if c["account_id"] == "4521")
        assert ring_case["narrative"] is None

        # opening the case drafts the SAR on demand
        opened = client.get(f"/api/case/{ring_case['case_id']}").json()
        assert opened["narrative"] == "LAZY SAR for 4521"


def test_clarification_query_starts_no_execution(monkeypatch):
    clarify_plan = {**ENTITY_PLAN, "clarification_needed": "Which time window should I analyze?"}
    monkeypatch.setattr(main, "plan_query", lambda q, c=None: clarify_plan)

    with TestClient(main.app) as client:
        submit = client.post("/api/query", json={"query": "is there suspicious activity?"}).json()
        assert submit["clarification_needed"] == "Which time window should I analyze?"
        results = client.get(f"/api/query/{submit['trace_id']}/results").json()
        assert results["results"] == []


def test_unknown_trace_and_case_return_404(monkeypatch):
    monkeypatch.setattr(main, "plan_query", lambda q, c=None: ENTITY_PLAN)
    with TestClient(main.app) as client:
        assert client.get("/api/query/deadbeef/trace").status_code == 404
        assert client.get("/api/case/CASE-nonexistent").status_code == 404
