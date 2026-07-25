"""risk_scorer — the weighted formula against fully hand-constructed inputs
(no CSV, no real dataset). `normalize_anomaly_score` is monkeypatched to an
identity pass-through so anomaly_component values are exactly what the test
supplies, rather than depending on the real 200k-row population's
median/p99 baseline — that keeps this a pure, fast, hand-checkable unit
test of the formula itself, independent of the live dataset.
"""

from __future__ import annotations

import pandas as pd
import pytest

import tools.risk_scorer as risk_scorer_module
from tools.graph_analysis import GraphFlag
from tools.risk_scorer import (
    HIGH_THRESHOLD, MEDIUM_THRESHOLD, WEIGHT_ANOMALY, WEIGHT_GRAPH, WEIGHT_RULES,
    risk_scorer,
)
from tools.rules_engine import Flag


@pytest.fixture(autouse=True)
def _identity_anomaly_normalization(monkeypatch):
    monkeypatch.setattr(risk_scorer_module, "normalize_anomaly_score", lambda s: s)


def _rule_flag(account_id: str, typology: str) -> Flag:
    return Flag(account_id=account_id, typology=typology, evidence={"reason": "test"})


def _graph_flag(account_id: str) -> GraphFlag:
    return GraphFlag(account_id=account_id, typology="FAN_IN_RING", evidence={"reason": "test"},
                      ring_accounts=[account_id])


def _scored(account_id: str, anomaly_score: float) -> pd.DataFrame:
    return pd.DataFrame([{"account_id": account_id, "anomaly_score": anomaly_score}])


def test_single_rule_hit_scores_below_saturation():
    records = risk_scorer([_rule_flag("A", "STRUCTURING")], [], None)
    rec = records[0]
    expected = WEIGHT_RULES * 0.5  # 1 of 2 typologies needed to saturate
    assert rec.score == pytest.approx(round(expected, 3))
    assert rec.risk_level == "LOW"
    assert rec.anomaly_component == 0.0
    assert "rules" in rec.explanation.lower()
    assert "graph" not in rec.explanation.lower()
    assert "anomaly" not in rec.explanation.lower()


def test_two_distinct_rules_saturate_the_rules_component():
    records = risk_scorer(
        [_rule_flag("A", "STRUCTURING"), _rule_flag("A", "VELOCITY")], [], None
    )
    rec = records[0]
    assert rec.score == pytest.approx(round(WEIGHT_RULES * 1.0, 3))
    assert rec.risk_level == "MEDIUM"
    assert MEDIUM_THRESHOLD <= rec.score < HIGH_THRESHOLD


def test_a_third_distinct_rule_does_not_increase_the_component_further():
    two = risk_scorer([_rule_flag("A", "STRUCTURING"), _rule_flag("A", "VELOCITY")], [], None)[0]
    three = risk_scorer(
        [_rule_flag("A", "STRUCTURING"), _rule_flag("A", "VELOCITY"), _rule_flag("A", "RAPID_MOVEMENT")], [], None
    )[0]
    assert three.score == two.score


def test_graph_only_hit_scores_from_graph_weight_alone():
    records = risk_scorer([], [_graph_flag("B")], None)
    rec = records[0]
    assert rec.score == pytest.approx(round(WEIGHT_GRAPH * 1.0, 3))
    assert rec.risk_level == "MEDIUM"
    assert "graph" in rec.explanation.lower()
    assert "rules" not in rec.explanation.lower()


def test_all_three_signals_saturated_scores_high_and_cites_all_three():
    records = risk_scorer(
        [_rule_flag("C", "STRUCTURING"), _rule_flag("C", "VELOCITY")],
        [_graph_flag("C")],
        _scored("C", 1.0),
    )
    rec = records[0]
    assert rec.score == pytest.approx(1.0)
    assert rec.risk_level == "HIGH"
    assert "rules" in rec.explanation.lower()
    assert "graph" in rec.explanation.lower()
    assert "anomaly" in rec.explanation.lower()


def test_anomaly_only_below_candidate_floor_is_not_a_candidate_at_all():
    """An account with NO rule/graph hit and an anomaly score under the
    0.5 candidate floor should not appear in the output at all — the floor
    exists so the anomaly model alone can't surface noise."""
    records = risk_scorer([], [], _scored("D", 0.49))
    assert records == []


def test_anomaly_only_at_exactly_the_candidate_floor_is_included():
    records = risk_scorer([], [], _scored("D", 0.5))
    assert len(records) == 1
    rec = records[0]
    assert rec.anomaly_only is True
    assert rec.score == pytest.approx(round(WEIGHT_ANOMALY * 0.5, 3))
    assert "anomaly" in rec.explanation.lower()


def test_skipped_anomaly_model_scores_correctly_with_only_rules():
    """scored_features=None (anomaly_model skipped) must behave identically
    to it never having contributed — not as a score of 0.0 on its own scale."""
    with_none = risk_scorer([_rule_flag("E", "STRUCTURING")], [], None)[0]
    with_empty = risk_scorer([_rule_flag("E", "STRUCTURING")], [], pd.DataFrame(columns=["account_id", "anomaly_score"]))[0]
    assert with_none.score == with_empty.score == pytest.approx(round(WEIGHT_RULES * 0.5, 3))
    assert with_none.anomaly_component == 0.0


def test_records_sorted_by_score_descending():
    records = risk_scorer(
        [_rule_flag("LOW-ONE", "STRUCTURING"),
         _rule_flag("HIGH-ONE", "STRUCTURING"), _rule_flag("HIGH-ONE", "VELOCITY")],
        [_graph_flag("HIGH-ONE")],
        None,
    )
    assert [r.account_id for r in records] == ["HIGH-ONE", "LOW-ONE"]


def test_high_and_medium_threshold_boundaries():
    # rules saturated (0.45) + graph (0.35) = 0.80 -> HIGH
    high = risk_scorer(
        [_rule_flag("H", "STRUCTURING"), _rule_flag("H", "VELOCITY")], [_graph_flag("H")], None
    )[0]
    assert high.score >= HIGH_THRESHOLD
    assert high.risk_level == "HIGH"

    # graph alone (0.35) -> MEDIUM (>= 0.30, < 0.60)
    medium = risk_scorer([], [_graph_flag("M")], None)[0]
    assert MEDIUM_THRESHOLD <= medium.score < HIGH_THRESHOLD
    assert medium.risk_level == "MEDIUM"

    # single rule alone (0.225) -> LOW (< 0.30)
    low = risk_scorer([_rule_flag("L", "STRUCTURING")], [], None)[0]
    assert low.score < MEDIUM_THRESHOLD
    assert low.risk_level == "LOW"
