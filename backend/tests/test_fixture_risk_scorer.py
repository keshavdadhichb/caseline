"""risk_scorer — the corroboration-tier logic against fully hand-constructed
inputs (no CSV, no real dataset). `is_anomaly_high` is monkeypatched to a
direct set-membership check so tier assignment is exactly what the test
supplies, rather than depending on the real 200k-row population's top-5%
threshold — that keeps this a pure, fast, hand-checkable unit test of the
tiering logic itself, independent of the live dataset.

Tier rule under test (see risk_scorer.py's module docstring):
  HIGH   = a strong rule fired AND corroborated by a second detection method (graph or anomaly-high)
  MEDIUM = exactly one of {rule, graph}
  LOW    = anomaly-high alone
"""

from __future__ import annotations

import pandas as pd
import pytest

import tools.risk_scorer as risk_scorer_module
from tools.graph_analysis import GraphFlag
from tools.risk_scorer import WEIGHT_ANOMALY, WEIGHT_GRAPH, WEIGHT_RULES, risk_scorer
from tools.rules_engine import Flag


@pytest.fixture(autouse=True)
def _controlled_anomaly(monkeypatch):
    """normalize_anomaly_score stays a pass-through (continuous value used
    for ranking); is_anomaly_high is driven by a simple >=1.0 check on
    that same pass-through value, so a test can request "high" by scoring
    an account at 1.0+ and "not high" by scoring it below 1.0."""
    monkeypatch.setattr(risk_scorer_module, "normalize_anomaly_score", lambda s: s)
    monkeypatch.setattr(risk_scorer_module, "is_anomaly_high", lambda s: s >= 1.0)


def _rule_flag(account_id: str, typology: str) -> Flag:
    return Flag(account_id=account_id, typology=typology, evidence={"reason": "test"})


def _graph_flag(account_id: str) -> GraphFlag:
    return GraphFlag(account_id=account_id, typology="FAN_IN_RING", evidence={"reason": "test"},
                      ring_accounts=[account_id])


def _scored(account_id: str, anomaly_score: float) -> pd.DataFrame:
    return pd.DataFrame([{"account_id": account_id, "anomaly_score": anomaly_score}])


def test_rule_alone_is_medium():
    records = risk_scorer([_rule_flag("A", "STRUCTURING")], [], None)
    rec = records[0]
    assert rec.risk_level == "MEDIUM"
    assert "rules" in rec.explanation.lower()
    assert "graph" not in rec.explanation.lower()
    assert "anomaly" not in rec.explanation.lower()


def test_graph_alone_is_medium():
    records = risk_scorer([], [_graph_flag("B")], None)
    rec = records[0]
    assert rec.risk_level == "MEDIUM"
    assert "graph" in rec.explanation.lower()
    assert "rules" not in rec.explanation.lower()


def test_multiple_distinct_rules_alone_is_still_medium_not_high():
    """Three rule typologies firing on the same account is still just ONE
    detection method (rules) — a rule alone is not corroborated by a second one."""
    records = risk_scorer(
        [_rule_flag("A", "STRUCTURING"), _rule_flag("A", "VELOCITY"), _rule_flag("A", "RAPID_MOVEMENT")], [], None
    )
    rec = records[0]
    assert rec.risk_level == "MEDIUM"


def test_rule_plus_graph_is_high():
    records = risk_scorer([_rule_flag("C", "STRUCTURING")], [_graph_flag("C")], None)
    rec = records[0]
    assert rec.risk_level == "HIGH"
    assert "rules" in rec.explanation.lower()
    assert "graph" in rec.explanation.lower()


def test_rule_plus_high_anomaly_is_high():
    records = risk_scorer([_rule_flag("D", "STRUCTURING")], [], _scored("D", 1.0))
    rec = records[0]
    assert rec.risk_level == "HIGH"
    assert "anomaly" in rec.explanation.lower()


def test_rule_plus_low_anomaly_stays_medium():
    """Anomaly score present but below the top-tier bar — not corroboration."""
    records = risk_scorer([_rule_flag("E", "STRUCTURING")], [], _scored("E", 0.5))
    rec = records[0]
    assert rec.risk_level == "MEDIUM"
    assert "anomaly" not in rec.explanation.lower()


def test_anomaly_alone_is_low():
    records = risk_scorer([], [], _scored("F", 1.0))
    rec = records[0]
    assert rec.risk_level == "LOW"
    assert rec.anomaly_only is True
    assert "anomaly" in rec.explanation.lower()


def test_anomaly_below_top_tier_alone_is_not_a_candidate_at_all():
    records = risk_scorer([], [], _scored("G", 0.9))
    assert records == []


def test_graph_plus_high_anomaly_without_a_rule_is_still_medium_not_high():
    """HIGH specifically requires a RULE plus corroboration — graph and
    anomaly agreeing with each other, with no rule at all, is not the
    documented HIGH condition."""
    records = risk_scorer([], [_graph_flag("H")], _scored("H", 1.0))
    rec = records[0]
    assert rec.risk_level == "MEDIUM"


def test_skipped_anomaly_model_none_never_contributes_to_high():
    with_none = risk_scorer([_rule_flag("I", "STRUCTURING")], [], None)[0]
    assert with_none.risk_level == "MEDIUM"
    assert with_none.anomaly_component == 0.0


def test_score_still_ranks_high_above_medium_above_low():
    records = risk_scorer(
        [_rule_flag("HIGH-ACCT", "STRUCTURING"), _rule_flag("MED-ACCT", "STRUCTURING")],
        [_graph_flag("HIGH-ACCT")],
        _scored("LOW-ACCT", 1.0),
    )
    levels = {r.account_id: r.risk_level for r in records}
    assert levels["HIGH-ACCT"] == "HIGH"
    assert levels["MED-ACCT"] == "MEDIUM"
    assert levels["LOW-ACCT"] == "LOW"
    scores = {r.account_id: r.score for r in records}
    assert scores["HIGH-ACCT"] > scores["MED-ACCT"] > scores["LOW-ACCT"]
    assert [r.account_id for r in records] == ["HIGH-ACCT", "MED-ACCT", "LOW-ACCT"]


def test_formula_weights_still_drive_the_ranking_score():
    # rules saturated (2 distinct typologies) + graph -> 0.45 + 0.35 = 0.80
    rec = risk_scorer(
        [_rule_flag("J", "STRUCTURING"), _rule_flag("J", "VELOCITY")], [_graph_flag("J")], None
    )[0]
    assert rec.score == pytest.approx(round(WEIGHT_RULES * 1.0 + WEIGHT_GRAPH * 1.0, 3))
    assert rec.risk_level == "HIGH"  # rule + graph corroboration, regardless of the score's magnitude


def test_records_include_anomaly_component_for_ranking_even_at_low_tier():
    rec = risk_scorer([], [], _scored("K", 1.0))[0]
    assert rec.score == pytest.approx(round(WEIGHT_ANOMALY * 1.0, 3))


def test_weak_rule_alone_never_reaches_high_even_with_corroboration():
    """STRUCTURING_MEDIUM is a WEAK indicator (see risk_scorer.py) — it
    can hold MEDIUM, but corroboration (graph or high anomaly) must not
    promote it to HIGH the way a strong rule's corroboration does."""
    with_graph = risk_scorer([_rule_flag("L", "STRUCTURING_MEDIUM")], [_graph_flag("L")], None)[0]
    assert with_graph.risk_level == "MEDIUM"

    with_anomaly = risk_scorer([_rule_flag("M", "STRUCTURING_MEDIUM")], [], _scored("M", 1.0))[0]
    assert with_anomaly.risk_level == "MEDIUM"


def test_weak_rule_scores_lower_than_strong_rule_for_ranking():
    strong = risk_scorer([_rule_flag("N", "VELOCITY")], [], None)[0]
    weak = risk_scorer([_rule_flag("O", "STRUCTURING_MEDIUM")], [], None)[0]
    assert weak.score < strong.score
