"""evals/run.py — runs every query in evals/queries.yaml against the live
API (`make backend` must be running), polls each to completion, and checks
the plan/results against expectations. Prints a pass/fail table.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import yaml

BASE_URL = "http://localhost:8000"
QUERIES_PATH = Path(__file__).parent / "queries.yaml"
POLL_TIMEOUT_S = 60


def load_queries() -> list[dict]:
    return yaml.safe_load(QUERIES_PATH.read_text())


def run_query(client: httpx.Client, spec: dict) -> list[str]:
    """Returns a list of failure descriptions (empty list = pass)."""
    failures: list[str] = []
    resp = client.post(f"{BASE_URL}/api/query", json={"query": spec["query"]})
    resp.raise_for_status()
    body = resp.json()
    plan = body["plan"]

    if spec.get("expect_clarification"):
        if not plan.get("clarification_needed"):
            failures.append("expected clarification_needed to be set, got None")
        return failures

    if plan.get("clarification_needed"):
        failures.append(f"unexpected clarification requested: {plan['clarification_needed']!r}")
        return failures

    steps = {s["tool"] for s in plan["steps"]}
    skipped = {s["tool"] for s in plan["skipped"]}
    for tool in spec.get("expect_steps_include", []):
        if tool not in steps:
            failures.append(f"expected step {tool!r} in plan — got steps={sorted(steps)}")
    for tool in spec.get("expect_skipped_include", []):
        if tool not in skipped:
            failures.append(f"expected {tool!r} to be skipped — got skipped={sorted(skipped)}")

    accounts = plan["filters"].get("accounts") or []
    for acct in spec.get("expect_filters_accounts_include", []):
        if acct not in accounts:
            failures.append(f"expected account {acct!r} in filters.accounts, got {accounts}")

    trace_id = body["trace_id"]
    t0 = time.time()
    while True:
        tr = client.get(f"{BASE_URL}/api/query/{trace_id}/trace").json()
        if tr["status"] != "running":
            break
        if time.time() - t0 > POLL_TIMEOUT_S:
            failures.append(f"execution did not finish within {POLL_TIMEOUT_S}s")
            return failures
        time.sleep(0.3)

    if tr["status"] != "done":
        failures.append(f"execution ended with status={tr['status']}")
        return failures

    results = client.get(f"{BASE_URL}/api/query/{trace_id}/results").json()["results"]

    expect_typologies = spec.get("expect_result_typology_include", [])
    if expect_typologies:
        seen = {t for r in results for t in (r["rules_fired"] + r["graph_fired"])}
        for typ in expect_typologies:
            if typ not in seen:
                failures.append(f"expected typology {typ!r} in results — got {sorted(seen)}")

    expect_account = spec.get("expect_result_account")
    if expect_account:
        by_account = {r["account_id"]: r for r in results}
        if expect_account not in by_account:
            failures.append(f"expected account {expect_account!r} in results ({len(results)} accounts scored)")
        else:
            expect_level = spec.get("expect_result_risk_level")
            actual_level = by_account[expect_account]["risk_level"]
            if expect_level and actual_level != expect_level:
                failures.append(f"expected {expect_account} risk_level={expect_level!r}, got {actual_level!r}")

    return failures


def main() -> int:
    queries = load_queries()
    client = httpx.Client(timeout=90.0)

    try:
        client.get(f"{BASE_URL}/api/health").raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: backend not reachable at {BASE_URL} ({exc}). Run `make backend` first.")
        return 1

    rows = []
    for spec in queries:
        failures = run_query(client, spec)
        rows.append((spec["id"], spec["query"], not failures, failures))
        status = "PASS" if not failures else "FAIL"
        print(f"[{status}] {spec['id']}: {spec['query']!r}")
        for f in failures:
            print(f"         - {f}")

    passed = sum(1 for _id, _q, ok, _f in rows if ok)
    total = len(rows)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
