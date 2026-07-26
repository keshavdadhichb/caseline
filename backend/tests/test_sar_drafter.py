"""Unit tests for sar_drafter's template fallback — kept network-independent
by monkeypatching _draft_live to fail. The live-drafting path (word count,
prose quality) is checked by evals/run.py against the real API.
"""

from tools import sar_drafter
from tools.case_builder import CaseFile


def _sample_case() -> CaseFile:
    return CaseFile(
        case_id="CASE-4521",
        account_id="4521",
        risk_level="HIGH",
        score=1.0,
        typologies=["STRUCTURING_HIGH", "RAPID_MOVEMENT", "FAN_IN_RING"],
        evidence=[
            {"typology": "STRUCTURING_HIGH", "source": "rules_engine", "count": 27, "window_days": 7},
            {"typology": "FAN_IN_RING", "source": "graph_analysis", "sender_count": 9,
             "total_in": 254317.25, "total_out": 216169.66, "consolidation_ratio": 0.85},
        ],
        timeline=[
            {"ts": "2022-09-11 08:00:00", "direction": "in", "counterparty": "RING-M01",
             "amount": 9412.5, "channel": "ACH", "txn_id": "S0000"},
            {"ts": "2022-09-17 20:00:00", "direction": "out", "counterparty": "RING-EXIT-01",
             "amount": 108172.33, "channel": "ACH", "txn_id": "S0027"},
        ],
        ring={"nodes": ["4521", "RING-M01"], "edges": []},
        recommended_action="report",
        explanation="HIGH (1.00) — rules: RAPID_MOVEMENT, STRUCTURING_HIGH; graph: FAN_IN_RING",
    )


def test_template_fallback_used_when_live_call_fails(monkeypatch, tmp_path):
    # point the disk cache at an empty dir so the fallback path is the
    # template, not a narrative cached by a real run
    monkeypatch.setattr(sar_drafter, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(sar_drafter, "_draft_live", lambda case: (_ for _ in ()).throw(RuntimeError("no network")))

    narrative = sar_drafter.draft_sar(_sample_case())

    assert "4521" in narrative
    assert "HIGH" in narrative
    assert "report" in narrative
    assert "template" in narrative.lower(), "fallback must disclose it's a template, not a live draft"


def test_disk_cache_replays_when_live_call_fails(monkeypatch, tmp_path):
    """A previously-drafted narrative on disk is replayed on later failure —
    this is what makes the flagship ring SAR survive a wifi-off demo."""
    monkeypatch.setattr(sar_drafter, "CACHE_DIR", tmp_path)
    case = _sample_case()

    # first call succeeds and writes to cache
    monkeypatch.setattr(sar_drafter, "_draft_live", lambda c: "LIVE NARRATIVE FOR 4521")
    first = sar_drafter.draft_sar(case)
    assert first == "LIVE NARRATIVE FOR 4521"

    # later call fails — must replay the cached narrative, not the template
    monkeypatch.setattr(sar_drafter, "_draft_live", lambda c: (_ for _ in ()).throw(RuntimeError("offline")))
    second = sar_drafter.draft_sar(case)
    assert second == "LIVE NARRATIVE FOR 4521"


def test_template_fallback_cites_only_amounts_present_in_evidence():
    case = _sample_case()
    narrative = sar_drafter._draft_template(case)

    # the template must cite a real evidence figure, not a fabricated one
    assert "254,317" in narrative or "$254,317.25" in narrative


def test_template_fallback_handles_case_with_no_evidence_or_timeline():
    """A degenerate case (shouldn't normally happen, but defends against a
    crash if it does) — must still return a non-empty string."""
    case = CaseFile(
        case_id="CASE-X", account_id="X", risk_level="LOW", score=0.1,
        recommended_action="monitor", explanation="LOW (0.10)",
    )
    narrative = sar_drafter._draft_template(case)
    assert narrative
    assert "X" in narrative
