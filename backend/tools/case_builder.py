"""case_builder — assembles a reviewable case file per flagged account:
entity, typologies, evidence table, timeline, ring subgraph (if any), and
a recommended escalation action. No LLM here — a case file is a direct,
inspectable assembly of what the rules/graph/model already found; only the
SAR narrative (sar_drafter) touches the LLM, and only after the case file
already contains every fact it's allowed to reference.

`build_indexes` pre-groups transactions and flags by account ONCE so
`case_builder` is O(that account's activity), not O(whole dataset). A broad
query can score 15k+ accounts; without the index each case did a full-frame
scan and the batch took minutes (150k rows x 15k cases). The executor also
caps how many full case files it builds — see CASE_BUILD_LIMIT there.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from tools.graph_analysis import GraphFlag
from tools.risk_scorer import RiskRecord
from tools.rules_engine import Flag

ACTION_BY_RISK = {"HIGH": "report", "MEDIUM": "flag for review", "LOW": "monitor"}
TIMELINE_MAX_ROWS = 25


@dataclass
class CaseFile:
    case_id: str
    account_id: str
    risk_level: str
    score: float
    typologies: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    ring: dict | None = None
    recommended_action: str = "monitor"
    explanation: str = ""
    narrative: str | None = None  # filled in by sar_drafter, HIGH cases only


@dataclass
class CaseIndexes:
    """Per-account lookups built once per query so each case file is cheap."""
    txn_rows: dict[str, list[int]]
    rule_flags: dict[str, list[Flag]]
    graph_flags: dict[str, list[GraphFlag]]


def build_indexes(
    df: pd.DataFrame, rule_flags: list[Flag], graph_flags: list[GraphFlag]
) -> CaseIndexes:
    txn_rows: dict[str, list[int]] = defaultdict(list)
    for pos, (frm, to) in enumerate(zip(df["from_account"].values, df["to_account"].values)):
        txn_rows[frm].append(pos)
        if to != frm:
            txn_rows[to].append(pos)

    rules_by_account: dict[str, list[Flag]] = defaultdict(list)
    for f in rule_flags:
        rules_by_account[f.account_id].append(f)

    graph_by_account: dict[str, list[GraphFlag]] = defaultdict(list)
    for gf in graph_flags:
        for acct in {gf.account_id, *gf.ring_accounts}:
            graph_by_account[acct].append(gf)

    return CaseIndexes(txn_rows, rules_by_account, graph_by_account)


def case_builder(record: RiskRecord, df: pd.DataFrame, idx: CaseIndexes) -> CaseFile:
    account_id = record.account_id

    own_rule_flags = idx.rule_flags.get(account_id, [])
    own_graph_flags = idx.graph_flags.get(account_id, [])

    evidence = (
        [{"typology": f.typology, "source": "rules_engine", **f.evidence} for f in own_rule_flags]
        + [{"typology": gf.typology, "source": "graph_analysis", **gf.evidence} for gf in own_graph_flags]
    )
    typologies = sorted({e["typology"] for e in evidence})

    own_txns = df.iloc[idx.txn_rows.get(account_id, [])]
    cited_txn_ids = {tid for f in own_rule_flags for tid in f.txn_ids}
    ranked = own_txns.assign(_cited=own_txns.txn_id.isin(cited_txn_ids))
    ranked = ranked.sort_values(["_cited", "amount"], ascending=[False, False]).head(TIMELINE_MAX_ROWS)
    ranked = ranked.sort_values("ts")

    timeline = [
        {
            "ts": str(row.ts),
            "direction": "out" if row.from_account == account_id else "in",
            "counterparty": row.to_account if row.from_account == account_id else row.from_account,
            "amount": float(row.amount),
            "channel": row.channel,
            "txn_id": row.txn_id,
        }
        for row in ranked.itertuples()
    ]

    ring = None
    if own_graph_flags:
        widest = max(own_graph_flags, key=lambda g: len(g.ring_accounts))
        ring = {
            "nodes": widest.ring_accounts,
            "edges": [{"from": a, "to": b, "amount": amt} for a, b, amt in widest.edges],
        }

    return CaseFile(
        case_id=f"CASE-{account_id}",
        account_id=account_id,
        risk_level=record.risk_level,
        score=record.score,
        typologies=typologies,
        evidence=evidence,
        timeline=timeline,
        ring=ring,
        recommended_action=ACTION_BY_RISK[record.risk_level],
        explanation=record.explanation,
    )
