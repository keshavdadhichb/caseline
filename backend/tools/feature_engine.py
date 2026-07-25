"""feature_engine — per-account rolling features computed from filtered txns.

Output: one row per account_id (an account counts whether it appears as
sender, receiver, or both) with the scalar features the rules engine and
anomaly model need. Every column traces to one plain, vectorized
computation — no hidden state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NEAR_THRESHOLD_LOW = 9_000.0
NEAR_THRESHOLD_HIGH = 10_000.0
RAPID_WINDOW_HOURS = 48

FEATURE_COLUMNS = [
    "account_id", "n_txns", "mean_amount", "std_amount",
    "hourly_count_mean", "hourly_count_std", "hourly_count_max",
    "near_threshold_count", "pct_near_threshold",
    "inbound_amount", "outbound_amount", "outbound_within_48h",
    "rapid_inout_ratio", "inbound_sender_count",
]


def feature_engine(df: pd.DataFrame) -> pd.DataFrame:
    """Build the per-account feature table from a filtered transactions frame."""
    if df.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)

    # Long form: one row per (account, direction) so an account's activity as
    # sender and as receiver both count toward "its" transaction history.
    sent = df.rename(columns={"from_account": "account_id"})[
        ["account_id", "ts", "amount", "txn_id"]
    ]
    recv = df.rename(columns={"to_account": "account_id"})[
        ["account_id", "ts", "amount", "txn_id"]
    ]
    long = pd.concat([sent, recv], ignore_index=True)

    grouped = long.groupby("account_id")
    n_txns = grouped.size().rename("n_txns")
    mean_amount = grouped["amount"].mean().rename("mean_amount")
    std_amount = grouped["amount"].std(ddof=0).fillna(0.0).rename("std_amount")

    near = long[(long.amount >= NEAR_THRESHOLD_LOW) & (long.amount < NEAR_THRESHOLD_HIGH)]
    near_count = near.groupby("account_id").size().rename("near_threshold_count")

    # Velocity: peak hourly txn count vs the account's own mean/std across
    # the filtered window.
    long_hour = long.assign(hour_bucket=long["ts"].dt.floor("h"))
    hourly = long_hour.groupby(["account_id", "hour_bucket"]).size()
    hourly_stats = hourly.groupby("account_id").agg(
        hourly_count_mean="mean", hourly_count_std="std", hourly_count_max="max"
    )
    hourly_stats["hourly_count_std"] = hourly_stats["hourly_count_std"].fillna(0.0)

    inbound = df.groupby("to_account")["amount"].sum().rename("inbound_amount")
    outbound = df.groupby("from_account")["amount"].sum().rename("outbound_amount")
    # Distinct senders, not just total inbound volume — RAPID_MOVEMENT uses
    # this to require money arriving from more than one source before a
    # fast in-and-out counts as a signal (a single counterparty sending
    # funds that promptly leave again is normal treasury/settlement
    # behavior, not a smurfing-style gather-and-scatter).
    inbound_sender_count = df.groupby("to_account")["from_account"].nunique().rename("inbound_sender_count")

    feat = pd.concat([n_txns, mean_amount, std_amount, near_count], axis=1).fillna(0.0)
    feat = feat.join(hourly_stats, how="left").fillna(0.0)
    feat = feat.join(inbound, how="left").join(outbound, how="left").fillna(0.0)
    feat = feat.join(inbound_sender_count, how="left").fillna(0.0)
    feat["pct_near_threshold"] = (feat["near_threshold_count"] / feat["n_txns"]).fillna(0.0)

    feat["outbound_within_48h"] = _outbound_within_48h(df)
    feat["outbound_within_48h"] = feat["outbound_within_48h"].fillna(0.0)
    feat["rapid_inout_ratio"] = np.where(
        feat["inbound_amount"] > 0,
        (feat["outbound_within_48h"] / feat["inbound_amount"]).clip(upper=1.0),
        0.0,
    )

    feat.index.name = "account_id"
    return feat.reset_index()[FEATURE_COLUMNS]


def _outbound_within_48h(df: pd.DataFrame) -> pd.Series:
    """For each account with inbound txns, sum outbound amount occurring
    within RAPID_WINDOW_HOURS of that account's LAST inbound txn in-window.
    Fully vectorized: merge + boolean mask + groupby, no per-account loop.
    """
    if df.empty:
        return pd.Series(dtype=float, name="outbound_within_48h")
    last_inbound = df.groupby("to_account")["ts"].max()
    out = df[["from_account", "ts", "amount"]].rename(columns={"from_account": "account_id"})
    out = out.join(last_inbound.rename("last_inbound_ts"), on="account_id", how="inner")
    window_end = out["last_inbound_ts"] + pd.Timedelta(hours=RAPID_WINDOW_HOURS)
    mask = (out["ts"] >= out["last_inbound_ts"]) & (out["ts"] <= window_end)
    result = out.loc[mask].groupby("account_id")["amount"].sum()
    result.name = "outbound_within_48h"
    return result
