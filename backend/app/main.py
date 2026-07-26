"""Caseline API — see CLAUDE.md for the frozen contract.

POST /api/query returns {trace_id, plan, clarification_needed} immediately
(if clarification_needed is set, no execution starts). Execution runs in a
FastAPI background task; trace events and results are polled separately so
the frontend's trace panel can show live progress.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from html import escape
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.executor import run_plan
from agent.planner import plan_query
from app.data_loader import known_accounts, load_transactions
from tools.anomaly_model import ANOMALY_TOP_PERCENTILE, N_ESTIMATORS, RANDOM_STATE
from tools.anomaly_model import _baseline as warm_anomaly_baseline
from tools.case_builder import CaseFile
from tools.graph_analysis import FAN_IN_MIN_SENDERS, FAN_IN_WINDOW_DAYS
from tools.narrate import describe_steps, explain_typologies, is_conceptual, prose_plan, prose_results
from tools.profile_data import profile_data
from tools.risk_scorer import FORMULA
from tools.sar_drafter import draft_sar

METHOD_METRICS_PATH = Path(__file__).resolve().parents[2] / "data" / "method_metrics.json"


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

# case_id -> case dict, populated as runs complete; backs GET /api/case/{id}
CASES: dict[str, dict] = {}


class QueryRequest(BaseModel):
    query: str
    clarification_answer: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@lru_cache(maxsize=1)
def _dataset_stats() -> dict:
    """Real dataset facts for the UI's About panel, landing line and footer
    strip — computed from the committed sample and the tool modules' own
    constants, never hardcoded in the frontend."""
    profile = profile_data(load_transactions())
    return {
        "dataset": "IBM HI-Small (Transactions for Anti-Money Laundering)",
        "n_txns": profile["n_txns"],
        "n_accounts": profile["n_accounts"],
        "date_range": profile["date_range"],
        "total_volume": profile["total_volume"],
        "median_amount": profile["median_amount"],
        "channels": profile["channel_breakdown"],
        "typologies": [
            "STRUCTURING_HIGH", "STRUCTURING_MEDIUM", "VELOCITY",
            "RAPID_MOVEMENT", "HIGH_RISK_AMOUNT", "FAN_IN_RING", "CYCLE",
        ],
        "model": {
            "name": "IsolationForest",
            "seed": RANDOM_STATE,
            "n_estimators": N_ESTIMATORS,
            "anomaly_top_percentile": ANOMALY_TOP_PERCENTILE,
        },
        "graph": {"fan_in_min_senders": FAN_IN_MIN_SENDERS, "fan_in_window_days": FAN_IN_WINDOW_DAYS},
        "scoring_formula": FORMULA,
        "determinism": f"Fixed seed {RANDOM_STATE}; every run reproduces exactly",
    }


@app.get("/api/stats")
def stats() -> dict:
    return _dataset_stats()


@app.get("/api/method")
def method() -> dict:
    """Baseline-vs-Caseline performance for the Method panel. Served from
    data/method_metrics.json, which `evals/baseline.py` generates on the
    held-out TEST split — so the panel shows exactly what was measured and
    can never drift from it. 404s rather than inventing numbers if the file
    was never generated."""
    if not METHOD_METRICS_PATH.exists():
        raise HTTPException(404, "method metrics not generated yet — run `make eval`")
    return json.loads(METHOD_METRICS_PATH.read_text())


@app.post("/api/query")
def submit_query(req: QueryRequest, background_tasks: BackgroundTasks) -> dict:
    plan = plan_query(req.query, req.clarification_answer)
    trace_id = uuid.uuid4().hex[:12]

    if plan.get("clarification_needed"):
        TRACES[trace_id] = {"status": "done", "events": [], "results": [], "cases": []}
        return {"trace_id": trace_id, "plan": plan, "clarification_needed": plan["clarification_needed"]}

    # An entity the dataset has never seen cannot be answered by scanning the
    # book: report it plainly instead of running a sweep and surfacing some
    # unrelated account's case as though it were the answer.
    requested = [str(a) for a in (plan.get("filters") or {}).get("accounts") or []]
    unknown = [a for a in requested if a not in known_accounts()]

    TRACES[trace_id] = {"status": "running", "events": [], "results": [], "cases": [], "plan": plan}
    if not (unknown and len(unknown) == len(requested)):
        background_tasks.add_task(_execute, trace_id, plan)
    else:
        TRACES[trace_id]["status"] = "done"
    # `prose` and `steps` are derived deterministically from the plan itself
    # (tools/narrate.py) — no second LLM call, and nothing asserted that the
    # plan doesn't already state. They ship with the POST so the thread can
    # narrate the moment the plan exists, before execution finishes.
    return {
        "trace_id": trace_id,
        "plan": plan,
        "clarification_needed": None,
        "prose": prose_plan(plan),
        "steps": describe_steps(plan),
        # The planner can decide a question needs no data work at all
        # ("what is structuring?"). That is NOT the same as a run that
        # executed and found nothing, and must not be reported as one —
        # no threshold was ever evaluated. Answer it instead, from the
        # rule modules' own constants.
        "conceptual": is_conceptual(plan),
        "typologies": explain_typologies() if is_conceptual(plan) else None,
        "unknown_accounts": unknown,
        # True when this plan is the generic offline fallback rather than a
        # plan built for this question. The UI must not narrate it as if the
        # agent understood the query.
        "degraded": bool(plan.get("_offline_fallback")),
        "served_from_cache": bool(plan.get("_served_from_cache")),
    }


def _execute(trace_id: str, plan: dict) -> None:
    trace = TRACES[trace_id]
    try:
        df = load_transactions()
        outcome = run_plan(df, plan, trace["events"])
        trace["results"] = outcome["results"]
        trace["cases"] = outcome["cases"]
        trace["status"] = "done"
        for case in outcome["cases"]:
            CASES[case["case_id"]] = case
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
    return {
        "results": t["results"],
        "cases": t["cases"],
        # Counts come straight off the result set — see tools/narrate.py.
        # A conceptual question ran nothing, so it gets no results sentence
        # at all rather than a false "nothing met the thresholds".
        "prose": None if is_conceptual(t.get("plan") or {}) else prose_results(t["results"], t["cases"]),
        "steps": describe_steps(t.get("plan") or {}, t["events"]),
        "conceptual": is_conceptual(t.get("plan") or {}),
    }


@app.get("/api/case/{case_id}")
def get_case(case_id: str) -> dict:
    case = CASES.get(case_id)
    if case is None:
        raise HTTPException(404, "unknown case_id")
    # SAR narratives are drafted lazily, on first open — the only time a SAR
    # is actually needed. HIGH cases are the "report" tier; MEDIUM/LOW don't
    # warrant a SAR. Cached (in memory + on disk) so a re-open is instant.
    if case.get("narrative") is None and case["risk_level"] == "HIGH":
        case["narrative"] = draft_sar(CaseFile(**case))
    return case


@app.get("/api/case/{case_id}/export", response_class=HTMLResponse)
def export_case(case_id: str) -> HTMLResponse:
    """Single-page printable case file. Deliberately self-contained HTML with
    print CSS rather than a server-side PDF: CLAUDE.md's contract allows
    either, weasyprint would add a heavy dependency for one button, and the
    browser's own "Save as PDF" produces the same artifact. Renders every
    fact from the stored case — no re-computation, so the exported document
    always matches what the analyst reviewed on screen."""
    case = CASES.get(case_id)
    if case is None:
        raise HTTPException(404, "unknown case_id")
    if case.get("narrative") is None and case["risk_level"] == "HIGH":
        case["narrative"] = draft_sar(CaseFile(**case))

    def row(k: str, v: str) -> str:
        return f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>"

    evidence_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(str(e.get("typology", ""))),
            escape(str(e.get("source", ""))),
            escape(str(e.get("reason", ""))),
        )
        for e in case.get("evidence", [])
    )
    timeline_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(str(t.get("ts", ""))), escape(str(t.get("direction", ""))),
            escape(str(t.get("counterparty", ""))), f"${float(t.get('amount', 0)):,.2f}",
        )
        for t in case.get("timeline", [])
    )
    narrative = case.get("narrative") or "No SAR narrative drafted for this risk tier."

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{escape(case['case_id'])} — Caseline case file</title>
<style>
@page {{ size: A4; margin: 18mm; }}
body {{ font-family: -apple-system, "DM Sans", system-ui, sans-serif; color:#494D5F; line-height:1.55; margin:0; }}
h1 {{ font-size:20px; margin:0 0 4px; letter-spacing:-0.02em; }}
h2 {{ font-size:11px; letter-spacing:0.09em; text-transform:uppercase; color:#9AA0B2; margin:24px 0 8px; }}
.meta {{ font-family:"DM Mono",ui-monospace,monospace; font-size:12px; color:#6E7385; }}
table {{ border-collapse:collapse; width:100%; font-size:12px; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #E1E7F2; vertical-align:top; }}
th {{ width:180px; font-weight:500; color:#6E7385; }}
.pill {{ display:inline-block; border-radius:999px; padding:4px 12px; font-size:12px;
        background:#FAEDF0; color:#9E4459; }}
p {{ font-size:13px; max-width:70ch; }}
@media print {{ .noprint {{ display:none; }} }}
</style></head><body>
<h1>Caseline case file</h1>
<div class="meta">{escape(case['case_id'])} · account {escape(case['account_id'])} · prepared by Caseline agent</div>
<p class="noprint" style="color:#9AA0B2;font-size:12px">Use your browser's Print → Save as PDF to file this document.</p>
<h2>Assessment</h2>
<div class="pill">{escape(case['risk_level'])} · recommended: {escape(case['recommended_action'])}</div>
<table>
{row("Risk score", case["score"])}
{row("Typologies", ", ".join(case.get("typologies", [])) or "—")}
{row("Scoring formula", FORMULA)}
{row("Explanation", case.get("explanation", ""))}
</table>
<h2>Why this was flagged</h2>
<table><tr><th>Typology</th><th>Source</th><th>Reason</th></tr>{evidence_rows or '<tr><td colspan=3>—</td></tr>'}</table>
<h2>Transaction timeline</h2>
<table><tr><th>Timestamp</th><th>Dir</th><th>Counterparty</th><th>Amount</th></tr>{timeline_rows or '<tr><td colspan=4>—</td></tr>'}</table>
<h2>SAR narrative</h2>
<p>{escape(narrative)}</p>
<h2>Provenance</h2>
<div class="meta">{escape(_dataset_stats()['dataset'])} · {_dataset_stats()['n_txns']:,} transactions ·
IsolationForest seed {RANDOM_STATE} · deterministic</div>
</body></html>"""
    return HTMLResponse(content=html)
