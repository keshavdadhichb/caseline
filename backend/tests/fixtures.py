"""Deterministic handcrafted fixture dataset for exact, independently-derived
assertions across every detection tool (see TESTING.md section 1 for the
full scenario catalogue).

Each scenario lives in its own >=100-day time epoch so scenarios can never
bleed into one another through a 30-day filter window, a 7-day
structuring/fan-in window, or a 48-hour rapid-movement window. Every
account name says what it's for; nothing is reused across scenarios except
SINK-1 (shared receiver for the three STRUCTURING sub-cases, which is
intentional and does not affect their individual account-level assertions).

Two of the four rules_engine typologies (VELOCITY, HIGH_RISK_AMOUNT) score a
single outlier against an account's own standard deviation. For b baseline
points at any value v and one outlier at any other value V (v != V), that
identity resolves to an EXACT closed form — independent of v and V:

    ddof=0 (population std):  z = sqrt(b)
    ddof=1 (sample std):      z = b / sqrt(b + 1)

Proof sketch (ddof=0 case): mean = (bv+V)/n, n=b+1. The outlier's deviation
is b(V-v)/n and each baseline point's deviation is (v-V)/n, so
sum_sq = (V-v)^2 * b/n, var = sum_sq/n, std = |V-v|*sqrt(b)/n, and
z = deviation/std = sqrt(b). The ddof=1 form follows the same way with
var = sum_sq/(n-1).

feature_engine.py is NOT internally consistent about which one it uses:
`std_amount` (feeds HIGH_RISK_AMOUNT) is computed with `.std(ddof=0)`
explicitly, but `hourly_count_std` (feeds VELOCITY) is computed with
pandas' groupby default `.agg("std")`, which is ddof=1. Both closed forms
are used below accordingly — see BURST_Z_COUNT (ddof=1) vs BURST_Z_AMOUNT
(ddof=0). This also means neither typology can hit its z=4.0 boundary
*exactly* at an integer b (solving b=16*sqrt(b+1) has no integer root), so
the "boundary" scenarios below are the tightest achievable integer pair
that straddles the line (b=16 just under, b=17 just over) rather than an
exact 4.0 — documented in TESTING.md as a real, minor spec inconsistency,
not fixed here since changing it is a product decision, not a test bug.

Either way, a single-account, single-outlier construction needs at least
~17 rows before it can cross a 4-sigma threshold at all: that lower bound
is why the velocity and high-risk-amount scenarios below look "large"
compared to the others — it's inherent to the statistic, not fixture bloat.
"""

from __future__ import annotations

import math

import pandas as pd

BASE = pd.Timestamp("2024-01-01 00:00:00")

_rows: list[dict] = []
_seq = [0]


def _add(ts: pd.Timestamp, frm: str, to: str, amount: float) -> str:
    _seq[0] += 1
    txn_id = f"F{_seq[0]:04d}"
    _rows.append({
        "txn_id": txn_id, "ts": ts, "from_account": frm, "to_account": to,
        "amount": round(amount, 2), "currency": "US Dollar", "channel": "ACH",
        "label": 0, "laundering_type": None,
    })
    return txn_id


def _epoch(n: int) -> pd.Timestamp:
    """Scenario groups start 100 days apart — far past any window this
    codebase uses (max is the 30-day filter default)."""
    return BASE + pd.Timedelta(days=100 * n)


# ---------------------------------------------------------------------------
# 1. STRUCTURING — positive, negative, boundary (all sender-side, into SINK-1)
# ---------------------------------------------------------------------------
_e = _epoch(0)
STRUCT_POS_TXN_IDS = [
    _add(_e + pd.Timedelta(days=d, hours=9), "STRUCT-POS", "SINK-1", amt)
    for d, amt in zip([0, 1, 2, 4, 5], [9200, 9400, 9600, 9800, 9850])
]
# rules_engine breaks on the FIRST window that clears the count-3 threshold,
# so evidence covers only the first 3 (days 0,1,2) even though all 5 qualify.
STRUCT_POS_EXPECTED_FLAG_TXN_IDS = STRUCT_POS_TXN_IDS[:3]

STRUCT_NEG_TXN_IDS = [
    _add(_e + pd.Timedelta(days=d, hours=9), "STRUCT-NEG", "SINK-1", amt)
    for d, amt in zip([0, 20, 40], [9100, 9300, 9500])
]  # 20-day gaps: no 3 ever fall inside a 7-day window

STRUCT_BOUND_TXN_IDS = [
    _add(_e + pd.Timedelta(days=d, hours=9), "STRUCT-BOUND", "SINK-1", 9000.00)
    for d in [0, 3, 6]
]  # exactly $9,000.00 == NEAR_THRESHOLD_LOW, inclusive boundary; count==3==MIN

# ---------------------------------------------------------------------------
# 2. FAN_IN_RING + RAPID_MOVEMENT positive (spec's own story: "fan-in plus
#    rapid movement" on one account) — 6 senders -> FANIN-AGG -> FI-EXIT
# ---------------------------------------------------------------------------
_e = _epoch(1)
FANIN_SENDERS = [f"FI-{i:02d}" for i in range(1, 7)]
for i, sender in enumerate(FANIN_SENDERS):
    _add(_e + pd.Timedelta(days=i, hours=10), sender, "FANIN-AGG", 2000.0)
FANIN_LAST_INBOUND = _e + pd.Timedelta(days=5, hours=10)
_add(FANIN_LAST_INBOUND + pd.Timedelta(hours=10), "FANIN-AGG", "FI-EXIT", 6000.0)
_add(FANIN_LAST_INBOUND + pd.Timedelta(hours=34), "FANIN-AGG", "FI-EXIT", 5040.0)
FANIN_TOTAL_IN = 12_000.0
FANIN_TOTAL_OUT = 11_040.0  # 92% of inbound, within 48h of the last deposit

# ---------------------------------------------------------------------------
# 3. CYCLE — 3-hop round-trip A -> B -> C -> A. Hops are spaced 3 days apart
#    (not 1) deliberately: cycle detection is purely topological (no time
#    window at all — see graph_analysis._cycles), but at 1-day spacing each
#    node's single outbound leg lands within 48h of its single inbound leg
#    and accidentally trips RAPID_MOVEMENT too (CYC-B hit exactly ratio
#    0.80 in an earlier version of this fixture). 3-day spacing (>48h) keeps
#    CYCLE a pure graph-only signature, isolated from the rules layer.
# ---------------------------------------------------------------------------
_e = _epoch(2)
_add(_e, "CYC-A", "CYC-B", 5000.0)
_add(_e + pd.Timedelta(days=3), "CYC-B", "CYC-C", 4000.0)
_add(_e + pd.Timedelta(days=6), "CYC-C", "CYC-A", 3000.0)

# ---------------------------------------------------------------------------
# 4. CLEAN accounts — must produce zero flags of any kind (false-positive
#    guard). Two small mutually-paying pairs, moderate/low-variance amounts,
#    spaced so no window/ratio/z-score condition is ever met.
# ---------------------------------------------------------------------------
_e = _epoch(3)
CLEAN_PAIR_1 = ("CLEAN-1", "CLEAN-2")
_clean1_amounts_days = [
    ("CLEAN-1", "CLEAN-2", 0, 300), ("CLEAN-2", "CLEAN-1", 2, 320),
    ("CLEAN-1", "CLEAN-2", 4, 280), ("CLEAN-2", "CLEAN-1", 6, 350),
    ("CLEAN-1", "CLEAN-2", 8, 310), ("CLEAN-2", "CLEAN-1", 10, 330),
]
for frm, to, d, amt in _clean1_amounts_days:
    _add(_e + pd.Timedelta(days=d), frm, to, amt)

_e = _epoch(4)
CLEAN_PAIR_2 = ("CLEAN-3", "CLEAN-4")
_clean2_amounts = [150, 180, 200, 170, 220, 160, 190, 210, 175, 205]
for i, amt in enumerate(_clean2_amounts):
    frm, to = ("CLEAN-3", "CLEAN-4") if i % 2 == 0 else ("CLEAN-4", "CLEAN-3")
    _add(_e + pd.Timedelta(days=2 * i), frm, to, amt)

CLEAN_ACCOUNTS = ["CLEAN-1", "CLEAN-2", "CLEAN-3", "CLEAN-4"]

# ---------------------------------------------------------------------------
# 5. Edge cases: single transaction (no divide-by-zero), zero outbound flow
# ---------------------------------------------------------------------------
_e = _epoch(5)
_add(_e, "SOLO-1", "PAYER-1", 500.0)  # SOLO-1's only transaction, ever
_add(_e + pd.Timedelta(days=3), "PAYER-1", "RECV-ONLY", 400.0)
_add(_e + pd.Timedelta(days=6), "PAYER-1", "RECV-ONLY", 450.0)
# RECV-ONLY receives twice, never sends -> outbound_amount == 0

# ---------------------------------------------------------------------------
# 6. VELOCITY + HIGH_RISK_AMOUNT positive, combined on one pair (BURST-DUAL
#    sends 17 baseline $100 txns in 17 distinct hours, then 2 more txns in
#    an 18th hour — one normal, one a $50,000 outlier). This single
#    18-hour/19-txn burst crosses BOTH thresholds at once:
#      hourly counts:  17 hours @ 1, 1 hour @ 2  -> b=17 -> z=sqrt(17)=4.1231
#      amounts:        18 entries @ 100, 1 @ 50000 -> b=18 -> z=sqrt(18)=4.2426
#    BURST-SINK receives the identical set of txns, so by the same math it
#    independently crosses both thresholds too (once as receiver) — this is
#    a real, intentional property of per-account features (an account's
#    feature row counts its activity regardless of direction), not a bug.
# ---------------------------------------------------------------------------
_e = _epoch(6)
for h in range(17):
    _add(_e + pd.Timedelta(hours=h), "BURST-DUAL", "BURST-SINK", 100.0)
_add(_e + pd.Timedelta(hours=17, minutes=0), "BURST-DUAL", "BURST-SINK", 100.0)
_add(_e + pd.Timedelta(hours=17, minutes=30), "BURST-DUAL", "BURST-SINK", 50_000.0)
BURST_OUTLIER_AMOUNT = 50_000.0
BURST_Z_COUNT = 17 / math.sqrt(18)  # hourly_count_std is ddof=1 (sample) -> b/sqrt(b+1)
BURST_Z_AMOUNT = math.sqrt(18)  # std_amount is ddof=0 (population) -> sqrt(b)

# ---------------------------------------------------------------------------
# 7. VELOCITY boundary — b=16 is the largest integer b whose z=b/sqrt(b+1)
#    still falls (just) under 4.0 (z=16/sqrt(17)=3.8808); paired with
#    BURST-DUAL's b=17 (z=17/sqrt(18)=4.0069, just over), these two are the
#    tightest achievable integer straddle of the VELOCITY_SIGMA=4.0 line.
# ---------------------------------------------------------------------------
_e = _epoch(7)
for h in range(16):
    _add(_e + pd.Timedelta(hours=h), "VBOUND-SRC", "VBOUND-SINK", 100.0)
_add(_e + pd.Timedelta(hours=16, minutes=0), "VBOUND-SRC", "VBOUND-SINK", 100.0)
_add(_e + pd.Timedelta(hours=16, minutes=30), "VBOUND-SRC", "VBOUND-SINK", 100.0)
VBOUND_Z_COUNT = 16 / math.sqrt(17)  # ~3.8808, just under the line

_e = _epoch(8)
for h in range(3):
    _add(_e + pd.Timedelta(hours=h), "VNEG-SRC", "VNEG-SINK", 100.0)
# 3 distinct hours, count==1 each -> hourly_count_std==0 -> guarded, skipped

# ---------------------------------------------------------------------------
# 8. HIGH_RISK_AMOUNT boundary (z==4.0 exactly -> NOT flagged) and negative.
#    Distinct hours/days throughout so no velocity signal is co-triggered.
# ---------------------------------------------------------------------------
_e = _epoch(9)
for d in range(16):
    _add(_e + pd.Timedelta(days=d), "HBOUND-SRC", "HBOUND-SINK", 100.0)
_add(_e + pd.Timedelta(days=16), "HBOUND-SRC", "HBOUND-SINK", 50_000.0)
HBOUND_Z_AMOUNT = math.sqrt(16)  # == 4.0 exactly

_e = _epoch(10)
for d, amt in zip(range(4), [100, 110, 95, 105]):
    _add(_e + pd.Timedelta(days=d), "HNEG-SRC", "HNEG-SINK", amt)

# ---------------------------------------------------------------------------
# 9. RAPID_MOVEMENT negative, boundary, and a materiality-floor regression
#    case (RAPID_MOVEMENT_MIN_INBOUND — see rules_engine.py history).
# ---------------------------------------------------------------------------
_e = _epoch(11)
_add(_e, "RNEG-IN", "RNEG", 5000.0)
_add(_e + pd.Timedelta(days=1), "RNEG", "RNEG-OUT", 2000.0)  # ratio 0.40 < 0.80

_e = _epoch(12)
_add(_e, "RBOUND-IN", "RBOUND", 1000.0)  # exactly at the materiality floor
_add(_e + pd.Timedelta(days=1), "RBOUND", "RBOUND-OUT", 800.0)  # ratio exactly 0.80

_e = _epoch(13)
_add(_e, "RFLOOR-IN", "RFLOOR", 999.99)  # just under the $1,000 floor
_add(_e + pd.Timedelta(days=1), "RFLOOR", "RFLOOR-OUT", 999.99)  # ratio 1.00 but immaterial

FIXTURE_COLUMNS = [
    "txn_id", "ts", "from_account", "to_account", "amount",
    "currency", "channel", "label", "laundering_type",
]


def build_fixture() -> pd.DataFrame:
    """Fresh copy of the fixture frame, dtyped like app.data_loader's
    load_transactions() (ts as datetime64, amount as float)."""
    df = pd.DataFrame(_rows, columns=FIXTURE_COLUMNS)
    df["ts"] = pd.to_datetime(df["ts"])
    df["amount"] = df["amount"].astype(float)
    return df.sort_values("ts").reset_index(drop=True)


TOTAL_ROWS = len(_rows)
TOTAL_ACCOUNTS = len({r["from_account"] for r in _rows} | {r["to_account"] for r in _rows})
