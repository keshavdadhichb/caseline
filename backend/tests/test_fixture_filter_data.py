"""filter_data — boundary/inclusivity tests on a small hand-built frame
(independent of the big cross-tool fixture; this tool's behavior only
depends on a handful of rows around each boundary)."""

from __future__ import annotations

import pandas as pd
import pytest

from tools.filter_data import filter_data

BASE = pd.Timestamp("2024-06-01 00:00:00")


def _df() -> pd.DataFrame:
    # ts values straddle a would-be 7-day cutoff exactly, so the boundary
    # (>= cutoff) is directly observable.
    rows = [
        ("T1", BASE - pd.Timedelta(days=7), "A", "B", 100.0, "Wire"),
        ("T2", BASE - pd.Timedelta(days=7) + pd.Timedelta(microseconds=1), "A", "B", 100.0, "Wire"),
        ("T3", BASE - pd.Timedelta(days=7) - pd.Timedelta(microseconds=1), "A", "B", 100.0, "Wire"),
        ("T4", BASE, "C", "D", 500.0, "ACH"),
        ("T5", BASE, "E", "F", 500.0, "ACH"),
    ]
    return pd.DataFrame(rows, columns=["txn_id", "ts", "from_account", "to_account", "amount", "channel"])


def test_window_days_cutoff_is_inclusive():
    out = filter_data(_df(), window_days=7)
    ids = set(out.txn_id)
    assert "T1" in ids, "a row exactly at the cutoff must be included (>=, not >)"
    assert "T2" in ids
    assert "T3" not in ids, "a row 1us before the cutoff must be excluded"


def test_min_amount_is_inclusive():
    out = filter_data(_df(), min_amount=500.0)
    assert set(out.txn_id) == {"T4", "T5"}
    out2 = filter_data(_df(), min_amount=500.000001)
    assert out2.empty


def test_account_filter_matches_either_side():
    out = filter_data(_df(), accounts=["D"])
    assert set(out.txn_id) == {"T4"}, "D is only ever a receiver — must still match"


def test_channel_filter_exact_match():
    out = filter_data(_df(), channel="ACH")
    assert set(out.txn_id) == {"T4", "T5"}


def test_combined_filters_are_conjunctive():
    out = filter_data(_df(), window_days=7, min_amount=400.0, channel="ACH")
    assert set(out.txn_id) == {"T4", "T5"}


def test_filter_matching_nothing_returns_empty_frame_not_raises():
    out = filter_data(_df(), accounts=["NOBODY"])
    assert out.empty
    assert list(out.columns) == list(_df().columns)


def test_original_frame_is_never_mutated():
    df = _df()
    snapshot = df.copy(deep=True)
    filter_data(df, window_days=1, min_amount=1000.0, accounts=["A"], channel="Wire")
    pd.testing.assert_frame_equal(df, snapshot)


def test_no_filters_returns_full_frame_reindexed():
    df = _df()
    out = filter_data(df)
    assert len(out) == len(df)
    assert list(out.index) == list(range(len(df)))
