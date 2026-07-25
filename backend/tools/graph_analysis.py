"""graph_analysis — networkx directed graph over filtered transactions.

Builds one account-level DiGraph per call (edges weighted by total amount);
detects fan-in rings and short round-trip cycles, and groups each finding
into a ring subgraph (nodes/edges) so the case file can render it as one
unit. Deliberately bounded (degree cap on cycle search, small hop limit) —
a real transaction graph has hub accounts with thousands of edges, and
unbounded cycle enumeration on those is combinatorially explosive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import pandas as pd

FAN_IN_MIN_SENDERS = 5
FAN_IN_WINDOW_DAYS = 7
FAN_IN_CONSOLIDATION_RATIO = 0.60  # receiver moves a majority of it back out
CYCLE_MAX_HOPS = 5
CYCLE_MAX_NODE_DEGREE = 25  # skip hub accounts — not what "round-tripping" means


@dataclass
class GraphFlag:
    account_id: str
    typology: str
    evidence: dict
    ring_accounts: list[str] = field(default_factory=list)
    edges: list[tuple[str, str, float]] = field(default_factory=list)


def build_graph(df: pd.DataFrame) -> nx.DiGraph:
    g = nx.DiGraph()
    for row in df.itertuples(index=False):
        if g.has_edge(row.from_account, row.to_account):
            g[row.from_account][row.to_account]["amount"] += row.amount
            g[row.from_account][row.to_account]["txn_ids"].append(row.txn_id)
        else:
            g.add_edge(row.from_account, row.to_account, amount=row.amount, txn_ids=[row.txn_id])
    return g


def graph_analysis(df: pd.DataFrame) -> list[GraphFlag]:
    if df.empty:
        return []
    g = build_graph(df)
    flags = _fan_in(df, g)
    flags += _cycles(g)
    return flags


def _fan_in(df: pd.DataFrame, g: nx.DiGraph) -> list[GraphFlag]:
    """>=5 distinct senders -> 1 receiver within 7 days, receiver then
    consolidates a majority of it back out (distinguishes a real "gather"
    ring from a merchant/payroll account that just has many payers).

    Quick-reject is vectorized (a single groupby.nunique() over the whole
    frame) so the expensive per-receiver windowed scan only runs on
    genuine candidates — iterating a Python-level loop over every one of
    ~140k receivers costs seconds on its own and blows the query budget.
    """
    sender_counts = df.groupby("to_account")["from_account"].nunique()
    candidates = sender_counts[sender_counts >= FAN_IN_MIN_SENDERS].index
    if len(candidates) == 0:
        return []

    flags: list[GraphFlag] = []
    window_seconds = FAN_IN_WINDOW_DAYS * 86_400
    for receiver, group in df[df["to_account"].isin(candidates)].groupby("to_account"):
        g2 = group.sort_values("ts").reset_index(drop=True)
        times = g2["ts"].tolist()
        senders = g2["from_account"].tolist()
        amounts = g2["amount"].tolist()

        # sliding window tracking distinct-sender count and dollar total
        # directly (no repeated .nunique()/.sum() calls). Among all windows
        # that clear the sender threshold, keep the one with the highest
        # dollar total — NOT just the first window to cross the sender
        # count, which would report only a partial slice of a longer-running
        # ring (e.g. the first 5 of 9 mules) and understate it.
        lo = 0
        counts: dict[str, int] = {}
        window_sum = 0.0
        best_lo, best_hi, best_unique, best_sum = -1, -1, 0, -1.0
        for hi in range(len(g2)):
            counts[senders[hi]] = counts.get(senders[hi], 0) + 1
            window_sum += amounts[hi]
            while (times[hi] - times[lo]).total_seconds() > window_seconds:
                counts[senders[lo]] -= 1
                if counts[senders[lo]] == 0:
                    del counts[senders[lo]]
                window_sum -= amounts[lo]
                lo += 1
            unique = len(counts)
            if unique >= FAN_IN_MIN_SENDERS and window_sum > best_sum:
                best_lo, best_hi, best_unique, best_sum = lo, hi, unique, window_sum

        if best_hi == -1:
            continue
        found = g2.iloc[best_lo:best_hi + 1]

        total_in = float(found["amount"].sum())
        out_edges = list(g.out_edges(receiver, data=True))
        total_out = sum(d["amount"] for _, _, d in out_edges)
        ratio = (total_out / total_in) if total_in > 0 else 0.0
        if ratio < FAN_IN_CONSOLIDATION_RATIO:
            continue

        ring_accounts = sorted(set(found["from_account"]) | {receiver})
        edges = [(r.from_account, r.to_account, float(r.amount)) for r in found.itertuples()]
        edges += [(receiver, dst, float(d["amount"])) for _, dst, d in out_edges]
        flags.append(GraphFlag(
            account_id=receiver,
            typology="FAN_IN_RING",
            evidence={
                "sender_count": best_unique,
                "window_days": FAN_IN_WINDOW_DAYS,
                "total_in": round(total_in, 2),
                "total_out": round(total_out, 2),
                "consolidation_ratio": round(ratio, 3),
                "reason": (
                    f"{best_unique} distinct accounts sent funds into "
                    f"{receiver} within {FAN_IN_WINDOW_DAYS} days; {ratio:.0%} consolidated back out"
                ),
            },
            ring_accounts=ring_accounts,
            edges=edges,
        ))
    return flags


def _cycles(g: nx.DiGraph) -> list[GraphFlag]:
    """Short round-trip cycles (<=5 hops) — funds return to (near) their
    origin, a classic layering signature. Restricted to a bounded-degree
    subgraph: hub accounts (banks, clearing houses) sit in huge numbers of
    incidental cycles that mean nothing and would blow up the search."""
    low_degree_nodes = [n for n in g.nodes if g.degree(n) <= CYCLE_MAX_NODE_DEGREE]
    sub = g.subgraph(low_degree_nodes)

    flags: list[GraphFlag] = []
    seen_cycles: set[frozenset] = set()
    for cycle in nx.simple_cycles(sub, length_bound=CYCLE_MAX_HOPS):
        if len(cycle) < 2:
            continue
        key = frozenset(cycle)
        if key in seen_cycles:
            continue
        seen_cycles.add(key)
        edges = []
        total = 0.0
        for i in range(len(cycle)):
            a, b = cycle[i], cycle[(i + 1) % len(cycle)]
            amt = sub[a][b]["amount"]
            edges.append((a, b, float(amt)))
            total += amt
        flags.append(GraphFlag(
            account_id=cycle[0],
            typology="CYCLE",
            evidence={
                "hops": len(cycle),
                "total_amount": round(total, 2),
                "reason": f"{len(cycle)}-hop round-trip: {' -> '.join(cycle)} -> {cycle[0]}",
            },
            ring_accounts=list(cycle),
            edges=edges,
        ))
    return flags
