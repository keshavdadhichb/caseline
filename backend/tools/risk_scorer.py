"""risk_scorer — combines rule hits + anomaly score + graph findings into
LOW/MEDIUM/HIGH with a weighted, printed formula. Hybrid scoring is the
story: rules give precision + explainability, the model catches what rules
miss, the graph catches networks — every score's explanation says which of
the three fired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tools.anomaly_model import normalize_anomaly_score
from tools.graph_analysis import GraphFlag
from tools.rules_engine import Flag

WEIGHT_RULES = 0.45
WEIGHT_GRAPH = 0.35
WEIGHT_ANOMALY = 0.20
FORMULA = (
    f"risk_score = {WEIGHT_RULES:.2f} x rules_component "
    f"+ {WEIGHT_GRAPH:.2f} x graph_component "
    f"+ {WEIGHT_ANOMALY:.2f} x anomaly_component"
)

RULES_SATURATION_COUNT = 2  # >=2 distinct rule typologies -> rules_component maxes at 1.0
ANOMALY_CANDIDATE_FLOOR = 0.5  # anomaly-only accounts still surface if the model is confident
HIGH_THRESHOLD = 0.60
MEDIUM_THRESHOLD = 0.30


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
    else:
        anomaly_norm = normalize_anomaly_score(
            scored_features.set_index("account_id")["anomaly_score"]
        )

    candidates = (
        set(rules_by_account)
        | set(graph_by_account)
        | set(anomaly_norm[anomaly_norm >= ANOMALY_CANDIDATE_FLOOR].index)
    )

    records: list[RiskRecord] = []
    for account_id in candidates:
        rules_fired = sorted(rules_by_account.get(account_id, set()))
        graph_fired = sorted(graph_by_account.get(account_id, set()))
        a_component = float(anomaly_norm.get(account_id, 0.0))

        rules_component = min(1.0, len(rules_fired) / RULES_SATURATION_COUNT)
        graph_component = 1.0 if graph_fired else 0.0

        score = (
            WEIGHT_RULES * rules_component
            + WEIGHT_GRAPH * graph_component
            + WEIGHT_ANOMALY * a_component
        )
        risk_level = (
            "HIGH" if score >= HIGH_THRESHOLD else
            "MEDIUM" if score >= MEDIUM_THRESHOLD else
            "LOW"
        )

        fired_summary = []
        if rules_fired:
            fired_summary.append(f"rules: {', '.join(rules_fired)}")
        if graph_fired:
            fired_summary.append(f"graph: {', '.join(graph_fired)}")
        if a_component >= ANOMALY_CANDIDATE_FLOOR:
            fired_summary.append(f"anomaly model: {a_component:.2f} (population-normalized)")

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
