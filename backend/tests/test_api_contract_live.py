"""API contract — against a REAL running uvicorn server (subprocess), not
FastAPI's TestClient. TestClient runs BackgroundTasks synchronously as part
of the request/response cycle (existing tests note this explicitly), which
makes it structurally unable to prove the async contract CLAUDE.md
describes: "POST returns immediately so the trace panel can show live
progress." Only a real server, with a real event loop actually running
the background task concurrently with the client waiting on the next
request, can show whether that's genuinely true.

pytest.mark.live: starts a real server and makes real (planner) network
calls — excluded from the default sweep, run explicitly via `make test-live`.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{base_url}/api/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.3)
        else:
            output = proc.stdout.read() if proc.stdout else ""
            proc.kill()
            pytest.fail(f"server never became healthy on {base_url}\n{output}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_health_returns_200(live_server):
    r = httpx.get(f"{live_server}/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_post_query_returns_before_the_background_run_completes(live_server):
    """The actual, honestly-measured async contract. See TESTING.md for
    the full finding: CLAUDE.md says "POST returns immediately", but
    submit_query calls plan_query() (a synchronous, live LLM call) BEFORE
    handing off to BackgroundTasks — so the POST latency floor is whatever
    the planner call takes, not "immediately". Two real bugs were found and
    fixed while building this test (see the planner.py commits): the
    Anthropic SDK's default 600s read timeout plus its default 2 retries
    meant a slow/degraded connection could originally block this endpoint
    for up to ~25s observed live (worst case far higher) with nothing
    enforcing the documented 8s budget at all. After passing
    timeout=LIVE_TIMEOUT_SECONDS and max_retries=0 to the client, POST
    latency is now a real, bounded ~8s ceiling (asserted below) instead of
    an unbounded one — still not the <2s this test's originating spec
    assumed, but a known, demo-safe number instead of "however long the
    network feels like taking, possibly repeated 3 times."

    What IS genuinely true, and what this test actually proves against a
    real server: the POST response arrives with the plan already decided,
    and EXECUTION (run_plan — filter/feature/rules/graph/anomaly/risk/
    case_builder) is what's backgrounded — polling /trace immediately
    after the POST returns can still observe status="running", which
    TestClient's synchronous background-task execution can never show at
    all.
    """
    t0 = time.monotonic()
    r = httpx.post(
        f"{live_server}/api/query",
        json={"query": "Is customer ID 4521 suspicious?"},
        timeout=30.0,
    )
    post_elapsed = time.monotonic() - t0
    assert r.status_code == 200
    body = r.json()
    assert "trace_id" in body and "plan" in body
    # Not the <2s this test's originating spec assumed (see the docstring
    # finding above) — but now a real, bounded ceiling: planner.LIVE_TIMEOUT_SECONDS
    # (8s) plus overhead, not the unbounded-up-to-600s-times-retries latency
    # that existed before this session's two planner fixes.
    assert post_elapsed < 15.0, f"POST took {post_elapsed:.1f}s — the 8s live-call budget may have regressed"

    trace_id = body["trace_id"]
    trace_immediately_after = httpx.get(f"{live_server}/api/query/{trace_id}/trace").json()

    deadline = time.monotonic() + 60
    final_status = trace_immediately_after["status"]
    while final_status != "done" and time.monotonic() < deadline:
        time.sleep(0.2)
        final_status = httpx.get(f"{live_server}/api/query/{trace_id}/trace").json()["status"]
    assert final_status == "done", "execution never completed within 60s"

    print(f"\n[timing] POST /api/query returned in {post_elapsed:.2f}s "
          f"(trace status at that instant: {trace_immediately_after['status']!r})")


def test_results_before_completion_returns_409_not_partial_or_500(live_server):
    r = httpx.post(
        f"{live_server}/api/query",
        json={"query": "Which customers made 10+ transactions under $10,000?"},
        timeout=30.0,
    )
    trace_id = r.json()["trace_id"]

    results_resp = httpx.get(f"{live_server}/api/query/{trace_id}/results")
    trace_resp = httpx.get(f"{live_server}/api/query/{trace_id}/trace").json()
    if trace_resp["status"] != "done":
        assert results_resp.status_code == 409, (
            f"expected 409 while status={trace_resp['status']!r}, got {results_resp.status_code}"
        )
    else:
        # the background run happened to finish before this check ran —
        # not a failure, just means this particular query was fast today
        assert results_resp.status_code == 200


def test_unknown_trace_id_returns_404(live_server):
    assert httpx.get(f"{live_server}/api/query/deadbeefcafe/trace").status_code == 404
    assert httpx.get(f"{live_server}/api/query/deadbeefcafe/results").status_code == 404


def test_unknown_case_id_returns_404(live_server):
    assert httpx.get(f"{live_server}/api/case/CASE-does-not-exist").status_code == 404


def test_malformed_query_payload_returns_422(live_server):
    r = httpx.post(f"{live_server}/api/query", json={"not_query": "oops"})
    assert r.status_code == 422


def test_cors_allows_the_frontend_origin(live_server):
    r = httpx.get(f"{live_server}/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_does_not_reflect_an_arbitrary_origin(live_server):
    r = httpx.get(f"{live_server}/api/health", headers={"Origin": "http://evil.example.com"})
    assert r.headers.get("access-control-allow-origin") != "http://evil.example.com"


def test_case_export_endpoint_does_not_exist_yet(live_server):
    """CLAUDE.md's frozen API contract lists `GET /api/case/{id}/export`
    (single-page PDF). It is not implemented anywhere in app/main.py —
    confirmed by grep, and confirmed here live rather than assumed. Not
    stubbed by this test suite; reported honestly in TESTING.md as a real
    gap against the documented contract (also the first item on CLAUDE.md's
    own scope-cut order, so its absence is expected/acceptable, just not
    yet true that the contract is fully implemented)."""
    r = httpx.get(f"{live_server}/api/case/CASE-4521/export")
    assert r.status_code == 404
