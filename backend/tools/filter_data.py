"""filter_data — date/account/amount/channel scoping.

Deterministic first step of nearly every plan. Two ways to scope time:
`window_days` is relative to the DATASET's max timestamp, not wall-clock
(the sample is historical, Sept 2022; see DATA.md), while `date_from` /
`date_to` take explicit calendar dates. An explicit range used to be
impossible to express, so "between September 10 and September 17" was
squashed into a relative window and silently analysed the wrong 563 rows
instead of the intended 9,380.
"""

from __future__ import annotations

import pandas as pd


def filter_data(
    df: pd.DataFrame,
    window_days: int | None = None,
    min_amount: float | None = None,
    accounts: list[str] | None = None,
    channel: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    out = df
    # An explicit range wins over a relative window: if the analyst named
    # dates, that is the scope they meant.
    if date_from or date_to:
        if date_from:
            out = out[out["ts"] >= pd.Timestamp(date_from)]
        if date_to:
            # inclusive of the whole end day, so "to 17 September" includes
            # everything that happened on the 17th
            end = pd.Timestamp(date_to)
            if end.normalize() == end:
                end = end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            out = out[out["ts"] <= end]
    elif window_days is not None:
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
