"""profile_data — summary-structure and edge-case tests."""

from __future__ import annotations

import pandas as pd

from tools.profile_data import profile_data
from tests.fixtures import build_fixture


def test_profile_data_summary_structure_on_full_fixture():
    df = build_fixture()
    p = profile_data(df)
    assert p["n_txns"] == len(df)
    assert p["n_accounts"] == len(set(df.from_account) | set(df.to_account))
    assert p["date_range"] == [str(df.ts.min()), str(df.ts.max())]
    assert p["total_volume"] == round(float(df.amount.sum()), 2)
    assert p["median_amount"] == round(float(df.amount.median()), 2)
    assert p["channel_breakdown"] == {"ACH": len(df)}


def test_profile_data_empty_frame_does_not_raise():
    empty = pd.DataFrame(columns=["from_account", "to_account", "amount", "ts", "channel"])
    p = profile_data(empty)
    assert p == {"n_txns": 0, "n_accounts": 0}
