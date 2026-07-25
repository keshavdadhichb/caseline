"""sar_drafter — turns a case file into a 150-250 word SAR narrative via one
LLM call. The prompt gives the model ONLY the case file's own facts
(typologies, evidence, timeline, recommended action) so the narrative can't
cite anything not already substantiated. Falls back to a template-based
narrative if the LLM call fails — case files must never ship empty
(CLAUDE.md resilience requirement).
"""

from __future__ import annotations

import json

import anthropic

from tools.case_builder import CaseFile

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You draft Suspicious Activity Report (SAR) narratives for a bank compliance team. "
    "Write a 150-250 word narrative covering who, what, when, the typology/pattern, the "
    "amounts involved, and the recommended action. Cite ONLY facts present in the case "
    "file JSON you are given — never invent transaction details, dates, or amounts not "
    "in the evidence. Write in formal compliance prose, third person, no markdown "
    "formatting, no headers."
)


def draft_sar(case: CaseFile) -> str:
    try:
        return _draft_live(case)
    except Exception:  # noqa: BLE001 — case files must never ship without a narrative
        return _draft_template(case)


def _draft_live(case: CaseFile) -> str:
    client = anthropic.Anthropic()
    payload = {
        "account_id": case.account_id,
        "risk_level": case.risk_level,
        "typologies": case.typologies,
        "evidence": case.evidence,
        "timeline": case.timeline[:10],
        "recommended_action": case.recommended_action,
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
    )
    for block in response.content:
        if block.type == "text" and block.text.strip():
            return block.text.strip()
    raise RuntimeError(f"sar_drafter: no text block in response (stop_reason={response.stop_reason})")


def _draft_template(case: CaseFile) -> str:
    """Deterministic fallback — no LLM, only facts already in the case file."""
    typ = ", ".join(case.typologies) if case.typologies else "suspicious activity"
    amounts = [
        e.get("total_in") or e.get("inbound_amount") or e.get("total_amount") or e.get("amount")
        for e in case.evidence
    ]
    amounts = [a for a in amounts if a]
    total = f"${max(amounts):,.2f}" if amounts else "an undetermined amount"
    first_ts = case.timeline[0]["ts"] if case.timeline else "an undetermined date"
    last_ts = case.timeline[-1]["ts"] if case.timeline else first_ts

    return (
        f"Account {case.account_id} was flagged for {typ}, risk level {case.risk_level}, "
        f"based on {len(case.evidence)} independent detection signal(s) covering activity "
        f"between {first_ts} and {last_ts}. The largest single amount cited in the "
        f"supporting evidence is approximately {total}. {case.explanation} "
        f"Recommended action: {case.recommended_action}. "
        "(Generated from a template because the live drafting service was unavailable; "
        "a compliance analyst should review and finalize before filing.)"
    )
