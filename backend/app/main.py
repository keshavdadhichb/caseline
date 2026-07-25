"""Caseline API — contract stub (H0).

The contract below is FROZEN (see CLAUDE.md). Endpoints currently return
canned responses so the frontend can build against them from minute one;
the executor replaces the canned path in the agent milestone.
"""

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Caseline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# trace_id -> {"status": ..., "events": [...], "results": ..., "cases": ...}
TRACES: dict[str, dict] = {}


class QueryRequest(BaseModel):
    query: str
    clarification_answer: str | None = None


CANNED_PLAN = {
    "intent": "detect_structuring",
    "filters": {"window_days": 30, "min_amount": None, "accounts": None},
    "typologies": ["structuring"],
    "steps": [
        {"tool": "filter_data", "params": {"window_days": 30}, "reason": "query scopes to 30d"},
        {"tool": "feature_engine", "params": {}, "reason": "structuring features needed"},
        {"tool": "rules_engine", "params": {"typologies": ["structuring"]}, "reason": "structuring rule requested"},
    ],
    "skipped": [
        {"tool": "graph_analysis", "reason": "no network pattern requested"},
        {"tool": "profile_data", "reason": "targeted query — full EDA not needed"},
    ],
    "clarification_needed": None,
}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/query")
def submit_query(req: QueryRequest) -> dict:
    trace_id = uuid.uuid4().hex[:12]
    TRACES[trace_id] = {
        "status": "done",
        "events": [
            {"step": "filter_data", "state": "done", "summary": "stub: 200,014 txns -> 41,203 in window"},
            {"step": "feature_engine", "state": "done", "summary": "stub: 4,102 account-windows"},
            {"step": "rules_engine", "state": "done", "summary": "stub: 3 STRUCTURING flags"},
        ],
        "results": [],
        "cases": [],
    }
    return {"trace_id": trace_id, "plan": CANNED_PLAN, "clarification_needed": None}


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
    if t["status"] != "done":
        raise HTTPException(409, "run not finished")
    return {"results": t["results"], "cases": t["cases"]}


@app.get("/api/case/{case_id}")
def get_case(case_id: str) -> dict:
    raise HTTPException(404, "no cases yet — stub")
