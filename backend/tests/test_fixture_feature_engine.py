"""feature_engine — exact per-account values, independently recomputed with
the stdlib `statistics` module (population variance/stdev, ddof=0) against
literal amount lists, rather than by re-deriving pandas' own computation.
Also covers the two documented divide-by-zero edge cases.
"""

from __future__ import annotations

import statistics

import pytest

from tools.feature_engine import feature_engine
from tests.fixtures import build_fixture

FEATURES = feature_engine(build_fixture())


def _row(account_id: str):
    r = FEATURES[FEATURES.account_id == account_id]
    assert not r.empty, f"{account_id} produced no feature row"
    return r.iloc[0]


def test_struct_pos_sender_only_account():
    row = _row("STRUCT-POS")
    amounts = [9200, 9400, 9600, 9800, 9850]
    assert row.n_txns == 5
    assert row.mean_amount == pytest.approx(statistics.mean(amounts))
    assert row.std_amount == pytest.approx(statistics.pstdev(amounts))
    assert row.near_threshold_count == 5
    assert row.pct_near_threshold == pytest.approx(1.0)
    assert row.inbound_amount == pytest.approx(0.0)
    assert row.outbound_amount == pytest.approx(sum(amounts))
    assert row.outbound_within_48h == pytest.approx(0.0), "never received, so no 48h anchor exists"
    assert row.rapid_inout_ratio == pytest.approx(0.0)
    assert row.hourly_count_max == 1
    assert row.hourly_count_std == pytest.approx(0.0), "5 distinct days -> 5 distinct hour buckets"


def test_fanin_agg_mixed_direction_account():
    row = _row("FANIN-AGG")
    values = [2000.0] * 6 + [6000.0, 5040.0]  # 6 receipts + 2 outbound legs
    assert row.n_txns == 8
    assert row.mean_amount == pytest.approx(statistics.mean(values))
    assert row.std_amount == pytest.approx(statistics.pstdev(values))
    assert row.near_threshold_count == 0
    assert row.inbound_amount == pytest.approx(12_000.0)
    assert row.outbound_amount == pytest.approx(11_040.0)
    assert row.outbound_within_48h == pytest.approx(11_040.0), "both outbound legs land inside 48h"
    assert row.rapid_inout_ratio == pytest.approx(11_040.0 / 12_000.0)
    assert row.hourly_count_std == pytest.approx(0.0), "8 distinct hour buckets -> no velocity signal here"


def test_single_transaction_account_no_divide_by_zero():
    row = _row("SOLO-1")
    assert row.n_txns == 1
    assert row.mean_amount == pytest.approx(500.0)
    assert row.std_amount == pytest.approx(0.0)
    assert row.hourly_count_std == pytest.approx(0.0)
    assert row.hourly_count_max == 1
    assert row.inbound_amount == pytest.approx(0.0)
    assert row.outbound_amount == pytest.approx(500.0)
    assert row.rapid_inout_ratio == pytest.approx(0.0)


def test_zero_outbound_flow_account():
    row = _row("RECV-ONLY")
    values = [400.0, 450.0]
    assert row.n_txns == 2
    assert row.mean_amount == pytest.approx(statistics.mean(values))
    assert row.std_amount == pytest.approx(statistics.pstdev(values))
    assert row.inbound_amount == pytest.approx(850.0)
    assert row.outbound_amount == pytest.approx(0.0)
    assert row.outbound_within_48h == pytest.approx(0.0)
    assert row.rapid_inout_ratio == pytest.approx(0.0), "inbound > 0 but zero outbound -> ratio 0, not NaN"


def test_clean_pair_moderate_amounts_both_directions():
    row = _row("CLEAN-1")
    values = [300, 280, 310, 320, 350, 330]  # sent [300,280,310] + received [320,350,330]
    assert row.n_txns == 6
    assert row.mean_amount == pytest.approx(statistics.mean(values))
    assert row.std_amount == pytest.approx(statistics.pstdev(values))
    assert row.near_threshold_count == 0
    assert row.outbound_within_48h == pytest.approx(0.0), "no outbound send falls within 48h of its last receipt"


def test_burst_dual_hourly_and_amount_stats_match_the_closed_form():
    from tests.fixtures import BURST_Z_COUNT, BURST_Z_AMOUNT

    row = _row("BURST-DUAL")
    assert row.n_txns == 19
    assert row.hourly_count_max == 2
    z_count = (row.hourly_count_max - row.hourly_count_mean) / row.hourly_count_std
    assert z_count == pytest.approx(BURST_Z_COUNT)

    values = [100.0] * 18 + [50_000.0]
    assert row.mean_amount == pytest.approx(statistics.mean(values))
    assert row.std_amount == pytest.approx(statistics.pstdev(values))
    z_amount = (50_000.0 - row.mean_amount) / row.std_amount
    assert z_amount == pytest.approx(BURST_Z_AMOUNT)


def test_empty_frame_returns_empty_typed_frame():
    import pandas as pd

    from tools.feature_engine import FEATURE_COLUMNS

    out = feature_engine(pd.DataFrame(columns=["from_account", "to_account", "amount", "ts", "txn_id"]))
    assert out.empty
    assert list(out.columns) == FEATURE_COLUMNS
