"""Unit tests for the hybrid layer: anomaly_model, graph_analysis, risk_scorer.

Proves the injected ring is caught by all three signal types working
together, that the risk_scorer explanation names each signal that fired
(CLAUDE.md requirement), and guards the timing fix in graph_analysis
against regressing (an earlier version took 4s+ per call from an
unvectorized quick-reject over ~140k receivers).
"""

import time

from app.data_loader import load_transactions
from tools.anomaly_model import anomaly_model
from tools.feature_engine import feature_engine
from tools.filter_data import filter_data
from tools.graph_analysis import graph_analysis
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine


def _filtered():
    return filter_data(load_transactions(), window_days=30)


def test_graph_analysis_catches_full_fan_in_ring():
    flags = graph_analysis(_filtered())
    ring = next((f for f in flags if f.typology == "FAN_IN_RING" and f.account_id == "4521"), None)
    assert ring is not None, "aggregator 4521 was not flagged as a fan-in ring"
    assert ring.evidence["sender_count"] == 9, "should capture all 9 mules, not a partial slice"
    assert set(ring.ring_accounts) == {"4521"} | {f"RING-M0{i}" for i in range(1, 10)}
    assert 0.75 <= ring.evidence["consolidation_ratio"] <= 0.95


def test_graph_analysis_completes_within_budget():
    """Regression guard: an earlier version's unvectorized quick-reject took
    4s+ on this call alone, threatening the <10s per-query budget."""
    t0 = time.time()
    graph_analysis(_filtered())
    assert time.time() - t0 < 3.0


def test_anomaly_model_scores_ring_aggregator_above_median():
    filtered = _filtered()
    features = feature_engine(filtered)
    scored = anomaly_model(features)
    row = scored[scored.account_id == "4521"]
    assert not row.empty
    assert row["anomaly_score"].iloc[0] > scored["anomaly_score"].median()


def test_risk_scorer_ring_aggregator_high_with_all_three_signals():
    filtered = _filtered()
    features = feature_engine(filtered)
    rule_flags = rules_engine(filtered, features)
    graph_flags = graph_analysis(filtered)
    scored = anomaly_model(features)

    records = risk_scorer(rule_flags, graph_flags, scored)
    ring = next(r for r in records if r.account_id == "4521")

    assert ring.risk_level == "HIGH"
    assert ring.rules_fired, "explanation must cite which rules fired"
    assert ring.graph_fired, "explanation must cite which graph pattern fired"
    assert "anomaly" in ring.explanation.lower(), "explanation must cite the anomaly model"
    assert "rules" in ring.explanation.lower()
    assert "graph" in ring.explanation.lower()


def test_risk_scorer_high_tier_is_small_minority():
    filtered = _filtered()
    features = feature_engine(filtered)
    rule_flags = rules_engine(filtered, features)
    graph_flags = graph_analysis(filtered)
    scored = anomaly_model(features)

    records = risk_scorer(rule_flags, graph_flags, scored)
    high = [r for r in records if r.risk_level == "HIGH"]
    assert len(records) > 0
    assert len(high) < 0.05 * len(records), (
        f"{len(high)}/{len(records)} candidates scored HIGH — thresholds are too loose"
    )


def test_full_hybrid_pipeline_within_budget():
    """The whole chain (filter -> feature -> rules -> graph -> anomaly ->
    risk) must comfortably clear the 10s per-query budget, leaving room
    for the planner LLM call and case-building on top."""
    t0 = time.time()
    filtered = _filtered()
    features = feature_engine(filtered)
    rule_flags = rules_engine(filtered, features)
    graph_flags = graph_analysis(filtered)
    scored = anomaly_model(features)
    risk_scorer(rule_flags, graph_flags, scored)
    assert time.time() - t0 < 8.0
