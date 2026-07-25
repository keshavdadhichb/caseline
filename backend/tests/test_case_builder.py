"""Unit tests for case_builder — proves the ring aggregator's case file
assembles correctly (typologies, evidence, ring subgraph, timeline,
escalation action) and that low-risk accounts get a lighter case with no
ring subgraph and the correct "monitor" action.
"""

from app.data_loader import load_transactions
from tools.anomaly_model import anomaly_model
from tools.case_builder import build_indexes, case_builder
from tools.feature_engine import feature_engine
from tools.filter_data import filter_data
from tools.graph_analysis import graph_analysis
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine


def _pipeline():
    df = load_transactions()
    filtered = filter_data(df, window_days=30)
    features = feature_engine(filtered)
    rule_flags = rules_engine(filtered, features)
    graph_flags = graph_analysis(filtered)
    scored = anomaly_model(features)
    records = risk_scorer(rule_flags, graph_flags, scored)
    idx = build_indexes(filtered, rule_flags, graph_flags)
    return filtered, idx, records


def test_case_builder_ring_aggregator_has_full_evidence():
    filtered, idx, records = _pipeline()
    record = next(r for r in records if r.account_id == "4521")

    case = case_builder(record, filtered, idx)

    assert case.case_id == "CASE-4521"
    assert case.risk_level == "HIGH"
    assert case.recommended_action == "report"
    assert {"STRUCTURING", "RAPID_MOVEMENT", "FAN_IN_RING"} <= set(case.typologies)
    assert case.ring is not None
    assert len(case.ring["nodes"]) == 10  # 4521 + 9 mules
    assert case.timeline, "expected a non-empty timeline"
    timestamps = [row["ts"] for row in case.timeline]
    assert timestamps == sorted(timestamps), "timeline must be chronological"
    for entry in case.evidence:
        assert "typology" in entry and "source" in entry


def test_case_builder_low_risk_account_has_no_ring():
    filtered, idx, records = _pipeline()
    low_record = next(r for r in records if r.risk_level == "LOW")

    case = case_builder(low_record, filtered, idx)

    assert case.risk_level == "LOW"
    assert case.recommended_action == "monitor"
    assert case.ring is None
    assert case.narrative is None  # only assigned by the executor for HIGH cases
