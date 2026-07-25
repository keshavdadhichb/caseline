"""filter_data — date/account/amount/channel scoping.

Deterministic first step of nearly every plan. `window_days` is computed
relative to the DATASET's max timestamp, not wall-clock time — the sample
is historical (Sept 2022); see DATA.md.
"""

from __future__ import annotations

import pandas as pd


def filter_data(
    df: pd.DataFrame,
    window_days: int | None = None,
    min_amount: float | None = None,
    accounts: list[str] | None = None,
    channel: str | None = None,
) -> pd.DataFrame:
    out = df
    if window_days is not None:
        cutoff = df["ts"].max() - pd.Timedelta(days=window_days)
        out = out[out["ts"] >= cutoff]
    if min_amount is not None:
        out = out[out["amount"] >= min_amount]
    if accounts:
        acct_set = set(accounts)
        out = out[out["from_account"].isin(acct_set) | out["to_account"].isin(acct_set)]
    if channel is not None:
        out = out[out["channel"] == channel]
    return out.reset_index(drop=True)
