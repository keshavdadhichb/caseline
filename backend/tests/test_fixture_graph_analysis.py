"""graph_analysis — fan-in and cycle detection on the shared fixture, plus
the documented empty-graph edge case. Cross-checked against the same
exhaustive "no unexpected flags" style assertion used for rules_engine.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tools.graph_analysis import graph_analysis
from tests.fixtures import build_fixture, FANIN_SENDERS, FANIN_TOTAL_IN, FANIN_TOTAL_OUT

DF = build_fixture()
FLAGS = graph_analysis(DF)


def test_fan_in_ring_finds_exactly_the_six_senders():
    fan_in = [f for f in FLAGS if f.typology == "FAN_IN_RING"]
    assert len(fan_in) == 1, "fan-in should fire on FANIN-AGG only, nowhere else in the fixture"
    flag = fan_in[0]
    assert flag.account_id == "FANIN-AGG"
    assert flag.evidence["sender_count"] == 6
    assert set(flag.ring_accounts) == set(FANIN_SENDERS) | {"FANIN-AGG"}
    assert flag.evidence["total_in"] == FANIN_TOTAL_IN
    assert flag.evidence["total_out"] == FANIN_TOTAL_OUT
    assert flag.evidence["consolidation_ratio"] == round(FANIN_TOTAL_OUT / FANIN_TOTAL_IN, 3)


def test_cycle_detection_finds_the_three_hop_cycle():
    cycles = [f for f in FLAGS if f.typology == "CYCLE"]
    assert len(cycles) == 1, "exactly one 3-hop cycle exists in the fixture"
    flag = cycles[0]
    assert set(flag.ring_accounts) == {"CYC-A", "CYC-B", "CYC-C"}
    assert flag.evidence["hops"] == 3
    assert flag.evidence["total_amount"] == pytest.approx(5000.0 + 4000.0 + 3000.0)


def test_no_cycles_reported_on_clean_or_unrelated_accounts():
    cycle_nodes = set()
    for f in FLAGS:
        if f.typology == "CYCLE":
            cycle_nodes.update(f.ring_accounts)
    assert cycle_nodes == {"CYC-A", "CYC-B", "CYC-C"}


def test_no_fan_in_reported_outside_fanin_agg():
    fan_in_accounts = {f.account_id for f in FLAGS if f.typology == "FAN_IN_RING"}
    assert fan_in_accounts == {"FANIN-AGG"}


def test_graph_with_no_edges_returns_empty_not_raises():
    empty = pd.DataFrame(columns=["from_account", "to_account", "amount", "ts", "txn_id"])
    assert graph_analysis(empty) == []


def test_reciprocal_two_hop_relationship_is_not_flagged_as_a_cycle():
    """Regression test: CLEAN-1/CLEAN-2 and CLEAN-3/CLEAN-4 are ordinary
    accounts that happen to pay each other back and forth (bill-splitting,
    a repaid loan). An earlier version of graph_analysis._cycles counted
    any 2-node reciprocal relationship as a "cycle" (A->B->A), which fired
    on both pairs here — that's not round-tripping/layering, just two
    parties transacting in both directions, and is extremely common in real
    data. The fix requires >=3 distinct hops for a CYCLE flag."""
    flagged_accounts = {f.account_id for f in FLAGS} | {a for f in FLAGS for a in f.ring_accounts}
    assert "CLEAN-1" not in flagged_accounts
    assert "CLEAN-2" not in flagged_accounts
    assert "CLEAN-3" not in flagged_accounts
    assert "CLEAN-4" not in flagged_accounts
