"""rules_engine — named typology rules over filtered transactions + account
features. Each rule is a small, readable function returning zero or more
flags. Every flag cites the evidence a reviewer needs: counts, amounts,
window, and the specific transaction ids — no black box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

STRUCTURING_MIN_COUNT = 3
STRUCTURING_WINDOW_DAYS = 7
NEAR_THRESHOLD_LOW = 9_000.0
NEAR_THRESHOLD_HIGH = 10_000.0
VELOCITY_SIGMA = 4.0
RAPID_MOVEMENT_RATIO = 0.80
RAPID_MOVEMENT_MIN_INBOUND = 1_000.0  # materiality floor — a $0.01 pass-through is not a signal
HIGH_RISK_AMOUNT_SIGMA = 4.0

ALL_TYPOLOGIES = ("structuring", "velocity", "rapid_movement", "high_risk_amount")


@dataclass
class Flag:
    account_id: str
    typology: str
    evidence: dict
    txn_ids: list[str] = field(default_factory=list)


def rules_engine(
    df: pd.DataFrame, features: pd.DataFrame, typologies: list[str] | None = None
) -> list[Flag]:
    """Run the requested typology rules (default: all four)."""
    active = set(typologies) if typologies else set(ALL_TYPOLOGIES)
    flags: list[Flag] = []
    if "structuring" in active:
        flags += _structuring(df)
    if "velocity" in active:
        flags += _velocity(features)
    if "rapid_movement" in active:
        flags += _rapid_movement(features)
    if "high_risk_amount" in active:
        flags += _high_risk_amount(df, features)
    return flags


def _structuring(df: pd.DataFrame) -> list[Flag]:
    """>=3 txns within 10% below the $10,000 threshold in any 7-day span,
    same account — either as sender or receiver. A mule sending 3
    sub-threshold deposits and an aggregator receiving 3+ from different
    mules are both structuring."""
    if df.empty:
        return []
    sent = df.rename(columns={"from_account": "account_id"})[["account_id", "ts", "amount", "txn_id"]]
    recv = df.rename(columns={"to_account": "account_id"})[["account_id", "ts", "amount", "txn_id"]]
    long = pd.concat([sent, recv], ignore_index=True)
    near = long[(long.amount >= NEAR_THRESHOLD_LOW) & (long.amount < NEAR_THRESHOLD_HIGH)]

    window_seconds = STRUCTURING_WINDOW_DAYS * 86_400
    flags: list[Flag] = []
    for account_id, group in near.groupby("account_id"):
        g = group.sort_values("ts")
        times = g["ts"].tolist()
        ids = g["txn_id"].tolist()
        amounts = g["amount"].tolist()

        lo = 0
        for hi in range(len(times)):
            while (times[hi] - times[lo]).total_seconds() > window_seconds:
                lo += 1
            if hi - lo + 1 >= STRUCTURING_MIN_COUNT:
                window_ids = ids[lo:hi + 1]
                window_amounts = amounts[lo:hi + 1]
                flags.append(Flag(
                    account_id=account_id,
                    typology="STRUCTURING",
                    evidence={
                        "count": hi - lo + 1,
                        "window_days": STRUCTURING_WINDOW_DAYS,
                        "window_start": str(times[lo]),
                        "window_end": str(times[hi]),
                        "amounts": window_amounts,
                        "reason": (
                            f"{hi - lo + 1} transactions between "
                            f"${NEAR_THRESHOLD_LOW:,.0f} and ${NEAR_THRESHOLD_HIGH:,.0f} "
                            f"within {STRUCTURING_WINDOW_DAYS} days"
                        ),
                    },
                    txn_ids=window_ids,
                ))
                break  # one flag per account is enough evidence
    return flags


def _velocity(features: pd.DataFrame) -> list[Flag]:
    """Peak hourly transaction count > baseline mean + 4 std (own history)."""
    flags: list[Flag] = []
    for _, row in features.iterrows():
        std = row["hourly_count_std"]
        if std <= 0:
            continue
        z = (row["hourly_count_max"] - row["hourly_count_mean"]) / std
        if z > VELOCITY_SIGMA:
            flags.append(Flag(
                account_id=row["account_id"],
                typology="VELOCITY",
                evidence={
                    "peak_hourly_count": int(row["hourly_count_max"]),
                    "baseline_mean": round(row["hourly_count_mean"], 2),
                    "baseline_std": round(std, 2),
                    "z_score": round(z, 2),
                    "reason": (
                        f"peak of {int(row['hourly_count_max'])} txns/hour vs baseline "
                        f"{row['hourly_count_mean']:.1f}±{std:.1f} (z={z:.1f})"
                    ),
                },
            ))
    return flags


def _rapid_movement(features: pd.DataFrame) -> list[Flag]:
    """>=80% of inbound funds moved out within 48h, on a materially-sized
    inflow (>= RAPID_MOVEMENT_MIN_INBOUND — a $0.01 pass-through trivially
    satisfies "80% moved out" and isn't a signal)."""
    flags: list[Flag] = []
    for _, row in features.iterrows():
        if row["inbound_amount"] < RAPID_MOVEMENT_MIN_INBOUND:
            continue
        ratio = row["rapid_inout_ratio"]
        if ratio >= RAPID_MOVEMENT_RATIO:
            flags.append(Flag(
                account_id=row["account_id"],
                typology="RAPID_MOVEMENT",
                evidence={
                    "inbound_amount": round(row["inbound_amount"], 2),
                    "outbound_within_48h": round(row["outbound_within_48h"], 2),
                    "ratio": round(ratio, 3),
                    "reason": (
                        f"{ratio:.0%} of ${row['inbound_amount']:,.0f} inbound moved out "
                        f"within 48h"
                    ),
                },
            ))
    return flags


def _high_risk_amount(df: pd.DataFrame, features: pd.DataFrame) -> list[Flag]:
    """Single transaction amount z-score > 4 vs the account's own history.
    Vectorized: only the small subset of hits is iterated in Python."""
    if df.empty:
        return []
    stats = features.set_index("account_id")[["mean_amount", "std_amount"]]

    sent = df[["txn_id", "amount", "from_account"]].rename(columns={"from_account": "account_id"})
    sent["side"] = "sender"
    recv = df[["txn_id", "amount", "to_account"]].rename(columns={"to_account": "account_id"})
    recv["side"] = "receiver"
    long = pd.concat([sent, recv], ignore_index=True)

    long = long.join(stats, on="account_id")
    long = long[long["std_amount"] > 0]
    long = long.assign(z_score=(long["amount"] - long["mean_amount"]) / long["std_amount"])
    hits = long[long["z_score"] > HIGH_RISK_AMOUNT_SIGMA]

    flags: list[Flag] = []
    for _, row in hits.iterrows():
        flags.append(Flag(
            account_id=row["account_id"],
            typology="HIGH_RISK_AMOUNT",
            evidence={
                "amount": round(float(row["amount"]), 2),
                "account_mean": round(float(row["mean_amount"]), 2),
                "account_std": round(float(row["std_amount"]), 2),
                "z_score": round(float(row["z_score"]), 2),
                "side": row["side"],
                "reason": (
                    f"${row['amount']:,.0f} vs own average ${row['mean_amount']:,.0f} "
                    f"(z={row['z_score']:.1f})"
                ),
            },
            txn_ids=[row["txn_id"]],
        ))
    return flags
