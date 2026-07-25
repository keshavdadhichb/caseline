"""Caseline API — see CLAUDE.md for the frozen contract.

POST /api/query returns {trace_id, plan, clarification_needed} immediately
(if clarification_needed is set, no execution starts). Execution runs in a
FastAPI background task; trace events and results are polled separately so
the frontend's trace panel can show live progress.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.executor import run_plan
from agent.planner import plan_query
from app.data_loader import load_transactions
from tools.anomaly_model import _baseline as warm_anomaly_baseline


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Warm the dataset + IsolationForest baseline once at process start so
    # the FIRST live query doesn't pay that cost mid-request.
    load_transactions()
    warm_anomaly_baseline()
    yield


app = FastAPI(title="Caseline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# trace_id -> {"status": "running"|"done"|"error", "events": [...],
#              "results": [...], "cases": [...], "error": str | None}
TRACES: dict[str, dict] = {}


class QueryRequest(BaseModel):
    query: str
    clarification_answer: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/query")
def submit_query(req: QueryRequest, background_tasks: BackgroundTasks) -> dict:
    plan = plan_query(req.query, req.clarification_answer)
    trace_id = uuid.uuid4().hex[:12]

    if plan.get("clarification_needed"):
        TRACES[trace_id] = {"status": "done", "events": [], "results": [], "cases": []}
        return {"trace_id": trace_id, "plan": plan, "clarification_needed": plan["clarification_needed"]}

    TRACES[trace_id] = {"status": "running", "events": [], "results": [], "cases": []}
    background_tasks.add_task(_execute, trace_id, plan)
    return {"trace_id": trace_id, "plan": plan, "clarification_needed": None}


def _execute(trace_id: str, plan: dict) -> None:
    trace = TRACES[trace_id]
    try:
        df = load_transactions()
        outcome = run_plan(df, plan, trace["events"])
        trace["results"] = outcome["results"]
        trace["cases"] = []  # case_builder/sar_drafter wired in next milestone
        trace["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — surface to the client instead of hanging forever
        trace["status"] = "error"
        trace["error"] = str(exc)


@app.get("/api/query/{trace_id}/trace")
def get_trace(trace_id: str) -> dict:
    t = TRACES.get(trace_id)
    if t is None:
        raise HTTPException(404, "unknown trace_id")
    return {"status": t["status"], "events": t["events"]}


@app.get("/api/query/{trace_id}/results")
def get_results(trace_id: str) -> dict:
    t = TRACES.get(trace_id)
    if t is None:
        raise HTTPException(404, "unknown trace_id")
    if t["status"] == "error":
        raise HTTPException(500, t.get("error", "execution failed"))
    if t["status"] != "done":
        raise HTTPException(409, "run not finished")
    return {"results": t["results"], "cases": t["cases"]}


@app.get("/api/case/{case_id}")
def get_case(case_id: str) -> dict:
    raise HTTPException(404, "no cases yet — stub")
