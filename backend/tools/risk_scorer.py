"""risk_scorer — combines rule hits + anomaly score + graph findings into
LOW/MEDIUM/HIGH via CORROBORATION between independent detection methods,
plus a weighted, printed score used for ranking within and across tiers.

Tier assignment is corroboration-based, not a single blended number
crossing a cutoff: a single detection method — even a confident one —
produced too many single-signal HIGH flags to be a credible "report this"
tier (see METHODOLOGY.md for the before/after numbers). The three
methods (rules, graph, anomaly model) fail in different, largely
independent ways, so two of them agreeing is real evidence in a way that
one strong signal alone is not:

  HIGH   — a named rule fired AND at least one of (graph finding, anomaly
           score in the population's own top tier). Two independent
           detection methods agreeing.
  MEDIUM — exactly one detection method fired: a rule alone, or a graph
           finding alone (with no rule).
  LOW    — anomaly score alone, with no rule and no graph corroboration.

`score` (the old weighted formula) is kept for ranking — "the top 50
accounts by risk score" (used for Precision@N in evals/baseline.py) needs
a continuous ordering, and tier alone can't rank within a 1000-account
MEDIUM bucket. It no longer determines risk_level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tools.anomaly_model import is_anomaly_high, normalize_anomaly_score
from tools.graph_analysis import GraphFlag
from tools.rules_engine import Flag

WEIGHT_RULES = 0.45
WEIGHT_GRAPH = 0.35
WEIGHT_ANOMALY = 0.20
FORMULA = (
    f"risk_score (ranking only) = {WEIGHT_RULES:.2f} x rules_component "
    f"+ {WEIGHT_GRAPH:.2f} x graph_component "
    f"+ {WEIGHT_ANOMALY:.2f} x anomaly_component; "
    "risk_level (tier) = HIGH if rule AND (graph OR anomaly-high), "
    "MEDIUM if exactly one of {rule, graph}, LOW if anomaly-high alone"
)

RULES_SATURATION_COUNT = 2  # >=2 distinct rule typologies -> rules_component maxes at 1.0


@dataclass
class RiskRecord:
    account_id: str
    risk_level: str
    score: float
    rules_fired: list[str] = field(default_factory=list)
    graph_fired: list[str] = field(default_factory=list)
    anomaly_component: float = 0.0
    anomaly_only: bool = False
    explanation: str = ""


def risk_scorer(
    rule_flags: list[Flag],
    graph_flags: list[GraphFlag],
    scored_features: pd.DataFrame | None,
) -> list[RiskRecord]:
    """`scored_features` is the anomaly_model output — pass None (not a
    placeholder DataFrame) when anomaly_model was skipped for this query.
    A placeholder score would get run through normalize_anomaly_score's
    population-percentile mapping and could land on a misleadingly
    high-looking value, fabricating a signal that never actually ran."""
    rules_by_account: dict[str, set[str]] = {}
    for f in rule_flags:
        rules_by_account.setdefault(f.account_id, set()).add(f.typology)

    graph_by_account: dict[str, set[str]] = {}
    for gf in graph_flags:
        for acct in {gf.account_id, *gf.ring_accounts}:
            graph_by_account.setdefault(acct, set()).add(gf.typology)

    if scored_features is None or scored_features.empty:
        anomaly_norm = pd.Series(dtype=float)
        anomaly_high = pd.Series(dtype=bool)
    else:
        raw = scored_features.set_index("account_id")["anomaly_score"]
        anomaly_norm = normalize_anomaly_score(raw)
        anomaly_high = is_anomaly_high(raw)

    high_anomaly_accounts = set(anomaly_high[anomaly_high].index)

    candidates = set(rules_by_account) | set(graph_by_account) | high_anomaly_accounts

    records: list[RiskRecord] = []
    for account_id in candidates:
        rules_fired = sorted(rules_by_account.get(account_id, set()))
        graph_fired = sorted(graph_by_account.get(account_id, set()))
        a_component = float(anomaly_norm.get(account_id, 0.0))
        a_is_high = account_id in high_anomaly_accounts

        has_rule = bool(rules_fired)
        has_graph = bool(graph_fired)

        if has_rule and (has_graph or a_is_high):
            risk_level = "HIGH"
        elif has_rule or has_graph:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"  # a_is_high alone — the only other way to be a candidate at all

        rules_component = min(1.0, len(rules_fired) / RULES_SATURATION_COUNT)
        graph_component = 1.0 if graph_fired else 0.0
        score = (
            WEIGHT_RULES * rules_component
            + WEIGHT_GRAPH * graph_component
            + WEIGHT_ANOMALY * a_component
        )

        fired_summary = []
        if rules_fired:
            fired_summary.append(f"rules: {', '.join(rules_fired)}")
        if graph_fired:
            fired_summary.append(f"graph: {', '.join(graph_fired)}")
        if a_is_high:
            fired_summary.append(f"anomaly model: {a_component:.2f} (population top-tier)")

        records.append(RiskRecord(
            account_id=account_id,
            risk_level=risk_level,
            score=round(score, 3),
            rules_fired=rules_fired,
            graph_fired=graph_fired,
            anomaly_component=round(a_component, 3),
            anomaly_only=not rules_fired and not graph_fired,
            explanation=(
                f"{risk_level} ({score:.2f}) — " + "; ".join(fired_summary)
                if fired_summary else
                f"{risk_level} ({score:.2f}) — no individual signal met threshold"
            ),
        ))

    records.sort(key=lambda r: r.score, reverse=True)
    return records
