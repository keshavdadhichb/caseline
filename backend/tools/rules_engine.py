"""rules_engine — named typology rules over filtered transactions + account
features. Each rule is a small, readable function returning zero or more
flags. Every flag cites the evidence a reviewer needs: counts, amounts,
window, and the specific transaction ids — no black box.

Thresholds below were tightened from an earlier, looser version (3-count
structuring with no consolidation check, no minimum-history guard on the
z-score rules) that mechanically flagged a large share of ordinary
transaction behavior. Every change here is justified by a written AML/FATF
rationale BEFORE it was measured against the data — see METHODOLOGY.md for
the full reasoning and the before/after numbers on the held-out test split.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# --- STRUCTURING (FATF: "Structuring / Smurfing") ---------------------------
# Deliberately keeping deposits under the reporting threshold, followed by
# moving the accumulated money onward, is the strongest form of the crime —
# the threshold evasion alone is what a busy, entirely legitimate cash
# business also does every week (a corner store depositing $9,200 most days
# is not laundering money; it just does a lot of cash business). What
# separates the two most clearly is what happens to the money AFTER it
# accumulates: a legitimate business leaves the funds in the account; a
# smurfing aggregator consolidates them out again quickly.
#
# A single flat rule using that full definition, though, is a HIGH-
# confidence-only rule — and a compliance team does not only work in
# confirmed cases. Real AML transaction-monitoring programs routinely run
# tiered indicators: a strong/definite-match rule that alone justifies
# escalation, and a weaker/possible-match rule that still lands on an
# analyst's queue for review even without the full pattern confirmed yet
# (the corroborating leg — a later consolidation, a second account joining
# the same pattern — often hasn't happened YET at query time, or happened
# somewhere the query's own scope doesn't reach). Two tiers, not one:
#
#   STRUCTURING_HIGH   — the strict definition: >=5 deposits (not 3) in a
#                         5% band (not 10%), RECEIVER side only, AND a
#                         confirmed >=60% consolidation out within 7 days.
#                         High confidence — the consolidation leg is
#                         directly observed, not assumed.
#   STRUCTURING_MEDIUM — the original, looser definition this rule used
#                         before that tightening: >=3 transactions in a
#                         10% band, sender OR receiver side, no
#                         consolidation requirement. Weaker on its own
#                         (closer to what an ordinary cash-heavy legitimate
#                         business can also produce), but still a real,
#                         named AML red flag worth a human look — not
#                         nothing, just not enough alone to report.
#
# risk_scorer.py treats these differently: STRUCTURING_HIGH is a "strong"
# rule that can support a HIGH tier alongside a second detection method;
# STRUCTURING_MEDIUM is a "weak" rule that keeps an account on the MEDIUM
# tier (or contributes partial ranking credit) but never promotes an
# account to HIGH by itself, however much corroboration it picks up — a
# weak indicator plus agreement is still a weak indicator, not a strong
# one. See risk_scorer.py's module docstring and METHODOLOGY.md.
STRUCTURING_WINDOW_DAYS = 7
NEAR_THRESHOLD_HIGH = 10_000.0

STRUCTURING_HIGH_MIN_COUNT = 5  # was 3 — a single busy week can produce 3-4 sub-threshold deposits innocently
STRUCTURING_HIGH_LOW = 9_500.0  # was 9,000 (10% band) — tightened to 5% below the threshold
STRUCTURING_CONSOLIDATION_RATIO = 0.60  # matches FAN_IN_CONSOLIDATION_RATIO — same "moved most of it on" bar
STRUCTURING_CONSOLIDATION_WINDOW_DAYS = 7  # the "then moving the money" leg's own follow-on window

STRUCTURING_MEDIUM_MIN_COUNT = 3  # the original threshold, kept for the weaker tier
STRUCTURING_MEDIUM_LOW = 9_000.0  # the original 10% band, kept for the weaker tier

# --- VELOCITY (general "significant deviation from the account's own
# established pattern" red flag — FATF guidance treats this as a
# monitoring indicator across most typologies, not a single named one) ---
# A standard deviation computed from a handful of transactions is not a
# statistically meaningful "baseline" — with 3-4 points, one busy hour
# looks like an extreme outlier purely from sample-size noise, not because
# the behavior actually changed. Requiring enough prior activity for the
# baseline to mean something is standard practice for any z-score-based
# rule, not a tuned choice.
VELOCITY_SIGMA = 4.0
MIN_HISTORY_FOR_BASELINE = 10  # below this, an account's own std is not a stable estimate

# --- RAPID_MOVEMENT (FATF: "Layering" via funnel/pass-through accounts) ----
# Keeps the existing 80%-out-within-48h condition and materiality floor
# (added in an earlier pass — a $0.01 pass-through trivially satisfies "80%
# moved out" and was never a real signal). New: require the inbound side to
# come from more than one counterparty. A single sender whose funds a
# receiving account promptly forwards on is routine settlement/treasury
# behavior (an escrow account, a payroll intermediary) — the funnel-account
# typology specifically means MULTIPLE sources gathering into one
# conduit before it scatters back out, which is what actually distinguishes
# a mule/pass-through account from an ordinary two-party relationship.
RAPID_MOVEMENT_RATIO = 0.80
RAPID_MOVEMENT_MIN_INBOUND = 1_000.0  # materiality floor — a $0.01 pass-through is not a signal
RAPID_MOVEMENT_MIN_SOURCES = 2  # a single in-and-out counterparty is normal, not funneling

# --- HIGH_RISK_AMOUNT (general "amount inconsistent with established
# profile" red flag — again a cross-typology indicator, not one named
# FATF category) ---
# Same statistical-stability rationale as VELOCITY: a z-score computed from
# a handful of transactions is not trustworthy.
HIGH_RISK_AMOUNT_SIGMA = 4.0
# (MIN_HISTORY_FOR_BASELINE, shared with VELOCITY, applies here too.)

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
        flags += _structuring_high(df)
        flags += _structuring_medium(df)
    if "velocity" in active:
        flags += _velocity(features)
    if "rapid_movement" in active:
        flags += _rapid_movement(features)
    if "high_risk_amount" in active:
        flags += _high_risk_amount(df, features)
    return flags


def _structuring_high(df: pd.DataFrame) -> list[Flag]:
    """>=STRUCTURING_MIN_COUNT deposits within 5% below the $10,000
    threshold in a 7-day span, INTO the same account (receiver side only —
    see the module docstring), AND a subsequent outbound consolidation of
    at least STRUCTURING_CONSOLIDATION_RATIO of that window's total within
    the following STRUCTURING_CONSOLIDATION_WINDOW_DAYS days."""
    if df.empty:
        return []
    recv = df.rename(columns={"to_account": "account_id"})[["account_id", "ts", "amount", "txn_id"]]
    near = recv[(recv.amount >= STRUCTURING_HIGH_LOW) & (recv.amount < NEAR_THRESHOLD_HIGH)]
    if near.empty:
        return []

    # Vectorized quick-reject BEFORE building any per-account outbound
    # lookup: only accounts with >= STRUCTURING_MIN_COUNT near-threshold
    # deposits in total (let alone within any 7-day window) can possibly
    # qualify. On the real 200k-row sample this cuts thousands of incidental
    # near-threshold receivers down to a handful of real candidates. An
    # earlier version grouped df.groupby("from_account") for EVERY account
    # in the whole dataset up front (100k+ tiny sorted frames) regardless
    # of whether that account was ever a structuring candidate at all —
    # that dict comprehension alone dominated the runtime (12s+ of a
    # 200k-row query's 8s total budget, all before a single window was
    # even checked).
    near_counts = near.groupby("account_id").size()
    quick_candidates = set(near_counts[near_counts >= STRUCTURING_HIGH_MIN_COUNT].index)
    if not quick_candidates:
        return []
    near = near[near.account_id.isin(quick_candidates)]

    outbound_by_account = {
        account_id: group[["ts", "amount"]].sort_values("ts")
        for account_id, group in df[df.from_account.isin(quick_candidates)].groupby("from_account")
    }

    window_seconds = STRUCTURING_WINDOW_DAYS * 86_400
    consolidation_seconds = STRUCTURING_CONSOLIDATION_WINDOW_DAYS * 86_400
    flags: list[Flag] = []
    for account_id, group in near.groupby("account_id"):
        g = group.sort_values("ts")
        times = g["ts"].tolist()
        ids = g["txn_id"].tolist()
        amounts = g["amount"].tolist()
        outbound = outbound_by_account.get(account_id)

        # Find the best QUALIFYING window (highest dollar total, same
        # pattern as graph_analysis._fan_in) with only cheap O(1)-per-step
        # sliding-window bookkeeping — no per-window consolidation check
        # here. An earlier version checked consolidation inside this loop
        # for every qualifying window and only stopped once one passed;
        # for an account with many near-threshold deposits that never
        # consolidate (an ordinary busy-but-legitimate account sitting
        # near the threshold), that meant a full outbound-history scan
        # per window, compounding into a real slowdown on the 200k-row
        # sample (test_full_hybrid_pipeline_within_budget's 8s budget blew
        # out to 100s+). Doing the expensive check once, only for the
        # single best candidate, fixed it.
        lo = 0
        window_sum = 0.0
        best_lo, best_hi, best_sum = -1, -1, -1.0
        for hi in range(len(times)):
            window_sum += amounts[hi]
            while (times[hi] - times[lo]).total_seconds() > window_seconds:
                window_sum -= amounts[lo]
                lo += 1
            if hi - lo + 1 >= STRUCTURING_HIGH_MIN_COUNT and window_sum > best_sum:
                best_lo, best_hi, best_sum = lo, hi, window_sum

        if best_hi == -1:
            continue

        window_ids = ids[best_lo:best_hi + 1]
        window_amounts = amounts[best_lo:best_hi + 1]
        total_in = best_sum

        outbound_after = 0.0
        if outbound is not None and total_in > 0:
            window_end = times[best_hi]
            consolidation_end = window_end + pd.Timedelta(seconds=consolidation_seconds)
            mask = (outbound["ts"] > window_end) & (outbound["ts"] <= consolidation_end)
            outbound_after = float(outbound.loc[mask, "amount"].sum())
        ratio = min(1.0, outbound_after / total_in) if total_in > 0 else 0.0

        if ratio < STRUCTURING_CONSOLIDATION_RATIO:
            continue  # count qualifies, but funds stayed put

        flags.append(Flag(
            account_id=account_id,
            typology="STRUCTURING_HIGH",
            evidence={
                "count": best_hi - best_lo + 1,
                "window_days": STRUCTURING_WINDOW_DAYS,
                "window_start": str(times[best_lo]),
                "window_end": str(times[best_hi]),
                "amounts": window_amounts,
                "consolidation_ratio": round(ratio, 3),
                "consolidation_window_days": STRUCTURING_CONSOLIDATION_WINDOW_DAYS,
                "reason": (
                    f"{best_hi - best_lo + 1} deposits between "
                    f"${STRUCTURING_HIGH_LOW:,.0f} and ${NEAR_THRESHOLD_HIGH:,.0f} "
                    f"within {STRUCTURING_WINDOW_DAYS} days, {ratio:.0%} of it moved back out "
                    f"within {STRUCTURING_CONSOLIDATION_WINDOW_DAYS} days"
                ),
            },
            txn_ids=window_ids,
        ))
    return flags


def _structuring_medium(df: pd.DataFrame) -> list[Flag]:
    """The original (pre-tightening) structuring rule, kept as a weaker
    tier: >=STRUCTURING_MEDIUM_MIN_COUNT txns in a 10% band, sender OR
    receiver side, no consolidation requirement. See module docstring."""
    if df.empty:
        return []
    sent = df.rename(columns={"from_account": "account_id"})[["account_id", "ts", "amount", "txn_id"]]
    recv = df.rename(columns={"to_account": "account_id"})[["account_id", "ts", "amount", "txn_id"]]
    long = pd.concat([sent, recv], ignore_index=True)
    near = long[(long.amount >= STRUCTURING_MEDIUM_LOW) & (long.amount < NEAR_THRESHOLD_HIGH)]
    if near.empty:
        return []

    near_counts = near.groupby("account_id").size()
    quick_candidates = set(near_counts[near_counts >= STRUCTURING_MEDIUM_MIN_COUNT].index)
    if not quick_candidates:
        return []
    near = near[near.account_id.isin(quick_candidates)]

    window_seconds = STRUCTURING_WINDOW_DAYS * 86_400
    flags: list[Flag] = []
    for account_id, group in near.groupby("account_id"):
        g = group.sort_values("ts")
        times = g["ts"].tolist()
        ids = g["txn_id"].tolist()
        amounts = g["amount"].tolist()

        lo = 0
        window_sum = 0.0
        best_lo, best_hi, best_sum = -1, -1, -1.0
        for hi in range(len(times)):
            window_sum += amounts[hi]
            while (times[hi] - times[lo]).total_seconds() > window_seconds:
                window_sum -= amounts[lo]
                lo += 1
            if hi - lo + 1 >= STRUCTURING_MEDIUM_MIN_COUNT and window_sum > best_sum:
                best_lo, best_hi, best_sum = lo, hi, window_sum

        if best_hi == -1:
            continue

        window_ids = ids[best_lo:best_hi + 1]
        window_amounts = amounts[best_lo:best_hi + 1]
        flags.append(Flag(
            account_id=account_id,
            typology="STRUCTURING_MEDIUM",
            evidence={
                "count": best_hi - best_lo + 1,
                "window_days": STRUCTURING_WINDOW_DAYS,
                "window_start": str(times[best_lo]),
                "window_end": str(times[best_hi]),
                "amounts": window_amounts,
                "reason": (
                    f"{best_hi - best_lo + 1} transactions between "
                    f"${STRUCTURING_MEDIUM_LOW:,.0f} and ${NEAR_THRESHOLD_HIGH:,.0f} "
                    f"within {STRUCTURING_WINDOW_DAYS} days (no consolidation confirmed — weaker indicator)"
                ),
            },
            txn_ids=window_ids,
        ))
    return flags


def _velocity(features: pd.DataFrame) -> list[Flag]:
    """Peak hourly transaction count > baseline mean + 4 std (own history),
    only evaluated once the account has enough history for that std to be
    a meaningful estimate."""
    flags: list[Flag] = []
    for _, row in features.iterrows():
        if row["n_txns"] < MIN_HISTORY_FOR_BASELINE:
            continue
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
                    "n_txns": int(row["n_txns"]),
                    "reason": (
                        f"peak of {int(row['hourly_count_max'])} txns/hour vs baseline "
                        f"{row['hourly_count_mean']:.1f}±{std:.1f} (z={z:.1f}, "
                        f"based on {int(row['n_txns'])} prior transactions)"
                    ),
                },
            ))
    return flags


def _rapid_movement(features: pd.DataFrame) -> list[Flag]:
    """>=80% of inbound funds moved out within 48h, on a materially-sized
    inflow (>= RAPID_MOVEMENT_MIN_INBOUND) gathered from more than one
    distinct sender (>= RAPID_MOVEMENT_MIN_SOURCES) — see module docstring
    for why both guards exist."""
    flags: list[Flag] = []
    for _, row in features.iterrows():
        if row["inbound_amount"] < RAPID_MOVEMENT_MIN_INBOUND:
            continue
        if row["inbound_sender_count"] < RAPID_MOVEMENT_MIN_SOURCES:
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
                    "inbound_sender_count": int(row["inbound_sender_count"]),
                    "reason": (
                        f"{ratio:.0%} of ${row['inbound_amount']:,.0f} inbound moved out "
                        f"within 48h, gathered from {int(row['inbound_sender_count'])} distinct senders"
                    ),
                },
            ))
    return flags


def _high_risk_amount(df: pd.DataFrame, features: pd.DataFrame) -> list[Flag]:
    """Single transaction amount z-score > 4 vs the account's own history,
    only evaluated once the account has enough history for that history to
    be meaningful. Vectorized: only the small subset of hits is iterated
    in Python."""
    if df.empty:
        return []
    stats = features.set_index("account_id")[["mean_amount", "std_amount", "n_txns"]]

    sent = df[["txn_id", "amount", "from_account"]].rename(columns={"from_account": "account_id"})
    sent["side"] = "sender"
    recv = df[["txn_id", "amount", "to_account"]].rename(columns={"to_account": "account_id"})
    recv["side"] = "receiver"
    long = pd.concat([sent, recv], ignore_index=True)

    long = long.join(stats, on="account_id")
    long = long[(long["std_amount"] > 0) & (long["n_txns"] >= MIN_HISTORY_FOR_BASELINE)]
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
                "n_txns": int(row["n_txns"]),
                "reason": (
                    f"${row['amount']:,.0f} vs own average ${row['mean_amount']:,.0f} "
                    f"(z={row['z_score']:.1f}, based on {int(row['n_txns'])} prior transactions)"
                ),
            },
            txn_ids=[row["txn_id"]],
        ))
    return flags
