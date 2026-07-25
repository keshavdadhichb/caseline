"""End-to-end — drives the complete flow exactly as the frontend does
(POST -> poll trace -> fetch results -> open a case) against a real running
server, for each of the 3 canonical queries, then asserts the headline
outcome: the injected synthetic ring is caught, attributed to account
4521, and produces a complete case file with a SAR narrative.

GET /api/case/{id}/export is skipped, not stubbed — see
test_api_contract_live.py::test_case_export_endpoint_does_not_exist_yet
and TESTING.md; it isn't implemented anywhere in app/main.py.
"""

from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.live

CANONICAL_QUERIES = [
    "Find structuring patterns in the last 30 days",
    "Which customers made 10+ transactions under $10,000?",
    "Is customer ID 4521 suspicious?",
]


def _run_query(base_url: str, query: str) -> dict:
    t0 = time.monotonic()
    submit = httpx.post(f"{base_url}/api/query", json={"query": query}, timeout=30.0).json()
    trace_id = submit["trace_id"]
    assert submit["clarification_needed"] is None, f"canonical query unexpectedly asked for clarification: {submit}"

    deadline = time.monotonic() + 60
    status = "running"
    while status not in ("done", "error") and time.monotonic() < deadline:
        time.sleep(0.3)
        status = httpx.get(f"{base_url}/api/query/{trace_id}/trace", timeout=10).json()["status"]
    assert status == "done", f"query {query!r} never completed (status={status})"

    results = httpx.get(f"{base_url}/api/query/{trace_id}/results", timeout=10).json()
    return {"plan": submit["plan"], "trace_id": trace_id, "results": results,
            "wall_time_s": round(time.monotonic() - t0, 2)}


@pytest.mark.parametrize("query", CANONICAL_QUERIES)
def test_canonical_query_end_to_end(live_server, query):
    outcome = _run_query(live_server, query)

    plan = outcome["plan"]
    assert plan["steps"], f"query-appropriate plan must have at least one step: {plan}"
    covered = {s["tool"] for s in plan["steps"]} | {s["tool"] for s in plan["skipped"]}
    assert len(covered) == 7, "every catalog tool must be accounted for (steps xor skipped)"

    results = outcome["results"]
    assert "results" in results and "cases" in results

    for record in results["results"]:
        assert record["explanation"], f"every scored account needs an explanation: {record}"
        if record["risk_level"] != "LOW":
            assert record["rules_fired"] or record["graph_fired"] or record["anomaly_component"] > 0, (
                f"a non-LOW record's explanation must name at least one signal that actually fired: {record}"
            )

    for case in results["cases"]:
        assert case["recommended_action"] in {"monitor", "flag for review", "report"}
        assert case["typologies"] or case["risk_level"] == "LOW"

    print(f"\n[e2e] {query!r} -> {len(results['results'])} scored, "
          f"{len(results['cases'])} cases, {outcome['wall_time_s']}s wall time")


def test_the_injected_ring_produces_a_complete_case_file_with_sar(live_server):
    outcome = _run_query(live_server, "Is customer ID 4521 suspicious?")
    results = outcome["results"]

    record = next(r for r in results["results"] if r["account_id"] == "4521")
    assert record["risk_level"] == "HIGH"
    assert {"STRUCTURING", "RAPID_MOVEMENT", "FAN_IN_RING"} <= (
        set(record["rules_fired"]) | set(record["graph_fired"])
    )

    case = next(c for c in results["cases"] if c["account_id"] == "4521")
    assert case["ring"] is not None
    assert len(case["ring"]["nodes"]) == 10  # 4521 + 9 mules
    assert case["recommended_action"] == "report"

    opened = httpx.get(f"{live_server}/api/case/{case['case_id']}", timeout=30.0).json()
    assert opened["narrative"], "opening a HIGH case must produce a SAR narrative (live or template)"
    word_count = len(opened["narrative"].split())
    assert 50 <= word_count <= 400, f"narrative length looks wrong: {word_count} words"
    assert "4521" in opened["narrative"]
