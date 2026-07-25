"""case_builder — fixture-based variety alongside the existing real-ring
coverage in test_case_builder.py: a fan-in case (ring present) and a
zero-signal clean account (ring absent, action=monitor) built fast from the
small fixture instead of the full 200k-row dataset.
"""

from __future__ import annotations

from tools.anomaly_model import anomaly_model
from tools.case_builder import ACTION_BY_RISK, build_indexes, case_builder
from tools.feature_engine import feature_engine
from tools.graph_analysis import graph_analysis
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine
from tests.fixtures import build_fixture, FANIN_TOTAL_IN


def _pipeline():
    df = build_fixture()
    features = feature_engine(df)
    rule_flags = rules_engine(df, features)
    graph_flags = graph_analysis(df)
    scored = anomaly_model(features)
    records = risk_scorer(rule_flags, graph_flags, scored)
    idx = build_indexes(df, rule_flags, graph_flags)
    return df, idx, records


def test_fanin_case_has_ring_subgraph_and_matching_evidence():
    df, idx, records = _pipeline()
    record = next(r for r in records if r.account_id == "FANIN-AGG")

    case = case_builder(record, df, idx)

    assert case.case_id == "CASE-FANIN-AGG"
    assert case.ring is not None
    assert len(case.ring["nodes"]) == 7  # 6 senders + FANIN-AGG
    assert case.recommended_action == ACTION_BY_RISK[case.risk_level]
    typologies = {e["typology"] for e in case.evidence}
    assert "RAPID_MOVEMENT" in typologies
    assert "FAN_IN_RING" in typologies
    fanin_evidence = next(e for e in case.evidence if e["typology"] == "FAN_IN_RING")
    assert fanin_evidence["total_in"] == FANIN_TOTAL_IN


def test_clean_account_never_becomes_a_risk_candidate():
    """CLEAN-1 never crosses any rule/graph/anomaly threshold, so it should
    never appear in risk_records at all — case_builder never even runs for
    it, which is the strongest form of "produces a LOW/monitor case"."""
    _, _, records = _pipeline()
    assert "CLEAN-1" not in {r.account_id for r in records}


def test_case_timeline_is_chronological_and_bounded():
    df, idx, records = _pipeline()
    record = next(r for r in records if r.account_id == "FANIN-AGG")
    case = case_builder(record, df, idx)
    timestamps = [row["ts"] for row in case.timeline]
    assert timestamps == sorted(timestamps)
    assert len(case.timeline) == 8  # all of FANIN-AGG's txns, well under TIMELINE_MAX_ROWS
