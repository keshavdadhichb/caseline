"""risk_scorer — combines rule hits + anomaly score + graph findings into
LOW/MEDIUM/HIGH via corroboration between detection methods, plus a
weighted, printed score used for ranking within and across tiers.

Tier assignment is corroboration-based, not a single blended number
crossing a cutoff: a single detection method — even a confident one —
produced too many single-method HIGH flags to be a credible "report this"
tier (see METHODOLOGY.md). Rules, graph, and the anomaly model fail in
different ways, so a rule being corroborated by a second detection method
is real evidence in a way one signal alone is not — though rules and the
anomaly model share several underlying features (near_threshold_count,
rapid_inout_ratio, std_amount feed both), so a rule+anomaly agreement is
weaker corroboration than rule+graph, which uses genuinely separate data.
See METHODOLOGY.md for the measured breakdown.

Not every rule counts equally toward HIGH. STRUCTURING_MEDIUM (and any
other future "weaker indicator" typology) is a real, named AML red flag
worth review, but on its own is closer to what ordinary legitimate
activity can also produce — compliance teams route strong/definite-match
indicators differently from weak/possible-match ones. So:

  HIGH   — a STRONG rule fired AND corroborated by a second detection
           method (a graph finding, or an anomaly score in the
           population's own top tier).
  MEDIUM — exactly one detection method fired (any rule alone — strong or
           weak — or a graph finding alone), OR a strong rule with only
           weak-rule company.
  LOW    — anomaly score alone, with no rule and no graph corroboration.

`score` (the old weighted formula) is kept for ranking — "the top 50
accounts by risk score" (used for Precision@N in evals/baseline.py) needs
a continuous ordering, and tier alone can't rank within a large MEDIUM
bucket. It no longer determines risk_level; weak rules contribute less to
it than strong ones (WEAK_RULE_WEIGHT < 1.0).
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
    "risk_level (tier) = HIGH if a STRONG rule fired AND corroborated by a second "
    "detection method (graph or anomaly-high), MEDIUM if exactly one method fired "
    "(or only weak rules), LOW if anomaly-high alone"
)

# Weak = a real but lower-confidence indicator (see module docstring) that
# can keep an account on MEDIUM but never promotes it to HIGH by itself.
WEAK_RULE_TYPOLOGIES = {"STRUCTURING_MEDIUM"}
WEAK_RULE_WEIGHT = 0.5  # a weak rule's contribution to the ranking score, vs 1.0 for a strong one

RULES_SATURATION_COUNT = 2  # >=2 strong-rule-equivalent units -> rules_component maxes at 1.0


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

        strong_rules = [t for t in rules_fired if t not in WEAK_RULE_TYPOLOGIES]
        has_strong_rule = bool(strong_rules)
        has_rule = bool(rules_fired)
        has_graph = bool(graph_fired)

        if has_strong_rule and (has_graph or a_is_high):
            risk_level = "HIGH"
        elif has_rule or has_graph:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"  # a_is_high alone — the only other way to be a candidate at all

        weighted_rule_units = sum(1.0 if t not in WEAK_RULE_TYPOLOGIES else WEAK_RULE_WEIGHT for t in rules_fired)
        rules_component = min(1.0, weighted_rule_units / RULES_SATURATION_COUNT)
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
