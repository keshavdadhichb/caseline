"""Caseline API — see CLAUDE.md for the frozen contract.

POST /api/query returns {trace_id, plan, clarification_needed} immediately
(if clarification_needed is set, no execution starts). Execution runs in a
FastAPI background task; trace events and results are polled separately so
the frontend's trace panel can show live progress.
"""

from __future__ import annotations

import json
import re
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
from tools import gemini
from tools.narrate import (
    describe_steps, explain_case_plainly, explain_typologies, is_conceptual,
    prose_plan, prose_results,
)
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


class ChatRequest(BaseModel):
    message: str


class ExplainRequest(BaseModel):
    case_id: str | None = None
    text: str | None = None
    question: str | None = None
    with_image: bool = True


class SpeakRequest(BaseModel):
    text: str


class TranscribeRequest(BaseModel):
    audio_b64: str
    mime_type: str = "audio/webm"


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


# Small talk is matched by a broad pattern plus a hard negative guard,
# rather than a literal word list. "who are u" slipped through an earlier
# exact-match list, reached the planner, timed out, and produced a
# 7,985-account sweep in answer to a greeting.
#
# The guard is what makes breadth safe: if a message mentions anything from
# the detection vocabulary it is NEVER treated as small talk, however
# chatty it looks. So "hi, find structuring" still reaches the planner.
_DETECTION_WORDS = re.compile(
    r"\b(account|accounts|customer|customers|transaction|transactions|txn|"
    r"structuring|smurf\w*|launder\w*|velocity|anomal\w*|ring|rings|cycle|"
    r"suspicious|flag\w*|risk|typolog\w*|deposit\w*|amount|threshold|"
    r"pattern|patterns|counterpart\w*|sar|case|cases|score|scores|"
    r"\$|\d{3,})\b",
    re.IGNORECASE,
)

_SMALL_TALK = re.compile(
    r"^\s*(?:"
    r"h+i+|h+e+y+|hell?o+|yo+|sup|wh?a+t'?s?\s*up|greetings|"
    r"good\s*(?:morning|afternoon|evening|day)|"
    r"th(?:an)?[kx]s?\s*(?:you|u)?|ty|cheers|ta|much\s+appreciated|"
    r"(?:so\s+)?who\s+(?:are|r)\s*(?:you|u)|"
    r"wh?at'?s?\s+(?:are|r|is|s)?\s*(?:you|u|this|thi?s)|"
    r"wh?at\s+(?:do|can)\s+(?:you|u)\s+do|"
    r"how\s+(?:do|does)\s+(?:you|u|this|it)\s+work|"
    r"help|about|info|hm+|ok(?:ay)?|k|cool|nice|great|awesome|"
    r"got\s+it|makes\s+sense|i\s+see|bye|goodbye|see\s+ya"
    r")[\s!.?,]*$",
    re.IGNORECASE,
)


def is_small_talk(message: str) -> bool:
    """True only when the message is conversational AND mentions nothing from
    the detection vocabulary."""
    if _DETECTION_WORDS.search(message):
        return False
    return bool(_SMALL_TALK.match(message))


def _small_talk_reply(message: str) -> dict:
    """Answer conversationally without planning or touching the data. Falls
    back to a fixed reply so this path works with no key and offline."""
    facts = None
    try:
        st = _dataset_stats()
        facts = (f"Dataset: {st['dataset']}, {st['n_txns']:,} transactions across "
                 f"{st['n_accounts']:,} accounts. Detects structuring, rapid movement, "
                 f"velocity, high-risk amounts, fan-in rings and round-trip cycles.")
    except Exception:  # noqa: BLE001 - conversational nicety, never fatal
        pass
    try:
        text = gemini.chat(message, facts)
        source = "gemini"
    except gemini.GeminiUnavailable:
        text = ("I look for money-laundering patterns in this transaction dataset. "
                "Try asking something like \"Find structuring patterns in the last 30 days\" "
                "or \"Is customer ID 4521 suspicious?\".")
        source = "deterministic"
    return {"text": text, "source": source}


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    return _small_talk_reply(req.message)


@app.post("/api/query")
def submit_query(req: QueryRequest, background_tasks: BackgroundTasks) -> dict:
    # Small talk never reaches the planner: it needs no plan, and skipping
    # the call keeps a greeting instant instead of costing a planning round
    # trip. Detection queries are entirely unaffected.
    if not req.clarification_answer and is_small_talk(req.query):
        reply = _small_talk_reply(req.query)
        trace_id = uuid.uuid4().hex[:12]
        TRACES[trace_id] = {"status": "done", "events": [], "results": [], "cases": []}
        return {
            "trace_id": trace_id, "plan": None, "clarification_needed": None,
            "conversational": True, "prose": reply["text"], "source": reply["source"],
            "steps": [], "conceptual": False, "unknown_accounts": [],
            "degraded": False, "served_from_cache": False, "typologies": None,
        }

    plan = plan_query(req.query, req.clarification_answer)
    trace_id = uuid.uuid4().hex[:12]

    if plan.get("clarification_needed"):
        TRACES[trace_id] = {"status": "done", "events": [], "results": [], "cases": []}
        return {"trace_id": trace_id, "plan": plan, "clarification_needed": plan["clarification_needed"]}

    # A degraded plan is the generic fallback: the planner never understood
    # this question, so the sweep it produces answers nothing. Asking a
    # greeting used to return 7,985 accounts this way. Hand it to the
    # conversational path instead, which either answers it properly or says
    # it could not be planned.
    if plan.get("_offline_fallback") and not req.clarification_answer:
        reply = _small_talk_reply(req.query)
        trace_id = uuid.uuid4().hex[:12]
        TRACES[trace_id] = {"status": "done", "events": [], "results": [], "cases": []}
        return {
            "trace_id": trace_id, "plan": None, "clarification_needed": None,
            "conversational": True, "prose": reply["text"], "source": reply["source"],
            "steps": [], "conceptual": False, "unknown_accounts": [],
            "degraded": True, "served_from_cache": False, "typologies": None,
        }

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
        # A conceptual question gets a conversational answer on top of the
        # deterministic typology cards; the cards remain the source of truth
        # for thresholds and are shown regardless.
        "conversational_text": (
            _small_talk_reply(req.query)["text"] if is_conceptual(plan) else None
        ),
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
        trace["profile"] = outcome.get("profile")
        trace["aggregation"] = outcome.get("aggregation")
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
        "prose": None if is_conceptual(t.get("plan") or {}) else prose_results(
            t["results"], t["cases"], t.get("profile"), t.get("aggregation")),
        "profile": t.get("profile"),
        "aggregation": t.get("aggregation"),
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


# ---------------------------------------------------------------------------
# Presentation layer (Gemini). Strictly additive: nothing below influences a
# plan, a score or a decision, and every route degrades to a deterministic
# answer so the demo is never blocked on an optional dependency.
# ---------------------------------------------------------------------------

@app.get("/api/presentation")
def presentation_capabilities() -> dict:
    """Lets the UI hide controls it cannot fulfil rather than offering a
    button that fails when the key is absent."""
    return {"gemini": gemini.is_configured()}


@app.post("/api/explain")
def explain(req: ExplainRequest) -> dict:
    """Plain-language explanation, with an optional illustration.

    `source` tells the caller which path produced the text so the UI can be
    honest about it: "gemini" when the model wrote it, "deterministic" when
    it came from the case's own figures via narrate.explain_case_plainly.
    """
    case = CASES.get(req.case_id) if req.case_id else None
    if req.case_id and case is None:
        raise HTTPException(404, "unknown case_id")

    payload: dict = case or {"text": req.text or ""}
    if not case and not req.text:
        raise HTTPException(422, "provide case_id or text")

    fallback = explain_case_plainly(case) if case else (req.text or "")
    try:
        text = gemini.explain_text(payload, req.question)
        source = "gemini"
    except gemini.GeminiUnavailable:
        text, source = fallback, "deterministic"

    image = None
    if req.with_image:
        subject = (
            f"An abstract diagram of a {', '.join(case.get('typologies', [])) or 'suspicious'} "
            "money-laundering pattern: several small accounts feeding one larger account "
            "which then forwards the funds onward."
        ) if case else "An abstract diagram illustrating a financial-crime detection concept."
        try:
            image = gemini.explain_image(subject)
        except gemini.GeminiUnavailable:
            image = None

    return {"text": text, "source": source, "image_b64": image}


@app.post("/api/speak")
def speak(req: SpeakRequest) -> dict:
    """Server-side speech. Returns base64 WAV, or asks the UI to fall back to
    the browser's own synthesiser, which needs no key and works offline."""
    try:
        return {"audio_b64": gemini.speak(req.text), "source": "gemini"}
    except gemini.GeminiUnavailable as exc:
        return {"audio_b64": None, "source": "browser", "reason": str(exc)}


@app.post("/api/transcribe")
def transcribe(req: TranscribeRequest) -> dict:
    try:
        return {"text": gemini.transcribe(req.audio_b64, req.mime_type), "source": "gemini"}
    except gemini.GeminiUnavailable as exc:
        raise HTTPException(503, f"speech-to-text unavailable: {exc}") from exc
