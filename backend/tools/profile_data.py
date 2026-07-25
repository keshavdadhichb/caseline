"""profile_data — quick EDA summary. Only runs for broad/overview queries
that need the big picture before any targeted detection makes sense; the
planner skips this for narrowly-scoped queries."""

from __future__ import annotations

import pandas as pd


def profile_data(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n_txns": 0, "n_accounts": 0}
    return {
        "n_txns": int(len(df)),
        "n_accounts": int(len(set(df.from_account) | set(df.to_account))),
        "date_range": [str(df.ts.min()), str(df.ts.max())],
        "total_volume": round(float(df.amount.sum()), 2),
        "median_amount": round(float(df.amount.median()), 2),
        "channel_breakdown": df.channel.value_counts().to_dict(),
    }
