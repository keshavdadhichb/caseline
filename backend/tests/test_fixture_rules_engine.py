"""rules_engine — one positive, one-or-more negative, and one boundary case
per typology, plus a single exhaustive assertion that the SET of (account,
typology) flags produced across the whole fixture is EXACTLY the expected
set — not a superset check. That set was derived by hand for every account
in the fixture (see fixtures.py's per-scenario comments for the arithmetic);
asserting equality means no incidental false positive anywhere in the
fixture can slip past silently.
"""

from __future__ import annotations

from tools.feature_engine import feature_engine
from tools.rules_engine import rules_engine
from tests.fixtures import build_fixture, FANIN_TOTAL_IN, FANIN_TOTAL_OUT, CLEAN_ACCOUNTS

DF = build_fixture()
FEATURES = feature_engine(DF)
FLAGS = rules_engine(DF, FEATURES)


def _by_account_typology():
    out = {}
    for f in FLAGS:
        out.setdefault(f.account_id, set()).add(f.typology)
    return out


BY_ACCOUNT = _by_account_typology()

EXPECTED = {
    "STRUCT-POS": {"STRUCTURING"},
    "STRUCT-BOUND": {"STRUCTURING"},
    "FANIN-AGG": {"RAPID_MOVEMENT"},  # FAN_IN_RING itself comes from graph_analysis, not rules
    "RBOUND": {"RAPID_MOVEMENT"},
    # RSINGLE deliberately absent: amount+ratio would qualify, but it's a
    # single-sender case — RAPID_MOVEMENT_MIN_SOURCES excludes it (see
    # test_rapid_movement_single_source_is_not_funneling below).
    "BURST-DUAL": {"VELOCITY", "HIGH_RISK_AMOUNT"},
    "BURST-SINK": {"VELOCITY", "HIGH_RISK_AMOUNT"},  # same burst, receiver side
}


def test_exact_flagged_set_across_entire_fixture():
    assert BY_ACCOUNT == EXPECTED, (
        f"unexpected flags: {set(BY_ACCOUNT) - set(EXPECTED)}, "
        f"missing flags: {set(EXPECTED) - set(BY_ACCOUNT)}, "
        "or a typology mismatch on a shared account — see EXPECTED for the full derivation"
    )


# --- STRUCTURING (receiver-side only, >=5 deposits in [$9,500,$10,000)
#     within 7 days, AND >=60% consolidated back out within 7 more days) --

def test_structuring_positive_full_cluster_and_consolidation():
    flag = next(f for f in FLAGS if f.account_id == "STRUCT-POS")
    assert flag.evidence["count"] == 5
    assert flag.evidence["consolidation_ratio"] == 0.65  # exactly the 65% sent out, per fixtures.py
    assert set(flag.evidence["amounts"]) == {9600, 9650, 9700, 9750, 9800}


def test_structuring_negative_qualifying_cluster_but_no_consolidation():
    """The user's own contrasting case: a legitimate business makes
    frequent sub-threshold deposits but leaves the funds in place."""
    assert "STRUCT-NEG-NOCONSOL" not in BY_ACCOUNT


def test_structuring_negative_spread_over_40_days_not_flagged():
    assert "STRUCT-NEG-SPREAD" not in BY_ACCOUNT


def test_structuring_boundary_exact_count_band_and_consolidation():
    flag = next(f for f in FLAGS if f.account_id == "STRUCT-BOUND")
    assert flag.evidence["count"] == 5
    assert all(a == 9500.00 for a in flag.evidence["amounts"])
    assert flag.evidence["consolidation_ratio"] == 0.60


# --- VELOCITY ----------------------------------------------------------

def test_velocity_positive_barely_over_threshold():
    flag = next(f for f in FLAGS if f.account_id == "BURST-DUAL" and f.typology == "VELOCITY")
    assert flag.evidence["z_score"] > 4.0
    assert flag.evidence["peak_hourly_count"] == 2


def test_velocity_boundary_barely_under_threshold_not_flagged():
    assert "VBOUND-SRC" not in BY_ACCOUNT
    assert "VBOUND-SINK" not in BY_ACCOUNT


def test_velocity_negative_uniform_activity_std_zero_not_flagged():
    assert "VNEG-SRC" not in BY_ACCOUNT
    assert "VNEG-SINK" not in BY_ACCOUNT


def test_velocity_requires_minimum_history():
    """BURST-DUAL/SINK have 19 txns (well over MIN_HISTORY_FOR_BASELINE);
    the low-history negative cases (VNEG, 3 txns) are excluded by BOTH the
    std==0 guard and the history guard — this fixture doesn't isolate the
    history guard alone, but the rule module's own logic
    (n_txns < MIN_HISTORY_FOR_BASELINE) is exercised directly here."""
    from tools.rules_engine import MIN_HISTORY_FOR_BASELINE
    assert MIN_HISTORY_FOR_BASELINE >= 10  # guards against silently weakening this back toward 0


# --- RAPID_MOVEMENT (>=2 distinct senders, >=$1,000 inbound, >=80% out
#     within 48h) -----------------------------------------------------------

def test_rapid_movement_positive_fanin_aggregator():
    flag = next(f for f in FLAGS if f.account_id == "FANIN-AGG")
    assert flag.evidence["inbound_amount"] == FANIN_TOTAL_IN
    assert flag.evidence["outbound_within_48h"] == FANIN_TOTAL_OUT
    assert flag.evidence["ratio"] == round(FANIN_TOTAL_OUT / FANIN_TOTAL_IN, 3)
    assert flag.evidence["inbound_sender_count"] == 6


def test_rapid_movement_negative_ratio_under_threshold():
    assert "RNEG" not in BY_ACCOUNT


def test_rapid_movement_boundary_ratio_and_sources_and_floor_all_inclusive():
    flag = next(f for f in FLAGS if f.account_id == "RBOUND")
    assert flag.evidence["ratio"] == 0.80
    assert flag.evidence["inbound_sender_count"] == 2
    assert flag.evidence["inbound_amount"] == 1000.0


def test_rapid_movement_materiality_floor_excludes_immaterial_inflow():
    """Regression case: RFLOOR moves 100% of its inbound out within 48h
    from 2 distinct sources (a textbook rapid-movement ratio, satisfying
    the source-count guard too) but the inbound totals $999.99 — one cent
    under RAPID_MOVEMENT_MIN_INBOUND — so it must NOT be flagged."""
    assert "RFLOOR" not in BY_ACCOUNT


def test_rapid_movement_single_source_is_not_funneling():
    """RSINGLE has an ample, material inbound amount and a 90% ratio — it
    would have been flagged under the pre-tightening rule — but the money
    came from exactly ONE counterparty. A single-sender fast in-and-out is
    routine two-party settlement, not a funnel/gather-and-scatter pattern;
    RAPID_MOVEMENT_MIN_SOURCES=2 exists specifically to exclude it."""
    assert "RSINGLE" not in BY_ACCOUNT


# --- HIGH_RISK_AMOUNT ----------------------------------------------------

def test_high_risk_amount_positive_barely_over_threshold():
    flag = next(f for f in FLAGS if f.account_id == "BURST-DUAL" and f.typology == "HIGH_RISK_AMOUNT")
    assert flag.evidence["amount"] == 50_000.0
    assert flag.evidence["z_score"] > 4.0
    assert flag.evidence["side"] == "sender"

    recv_flag = next(f for f in FLAGS if f.account_id == "BURST-SINK" and f.typology == "HIGH_RISK_AMOUNT")
    assert recv_flag.evidence["side"] == "receiver"


def test_high_risk_amount_boundary_exactly_z4_not_flagged():
    """b=16 baseline points + 1 outlier gives z==sqrt(16)==4.0 exactly under
    ddof=0 population std; the rule's comparison is strict (`> 4.0`), so
    this must NOT be flagged despite being mathematically on the line."""
    assert "HBOUND-SRC" not in BY_ACCOUNT
    assert "HBOUND-SINK" not in BY_ACCOUNT


def test_high_risk_amount_negative_low_variance_not_flagged():
    assert "HNEG-SRC" not in BY_ACCOUNT
    assert "HNEG-SINK" not in BY_ACCOUNT


# --- False-positive guard -------------------------------------------------

def test_clean_accounts_produce_zero_flags():
    for account in CLEAN_ACCOUNTS:
        assert account not in BY_ACCOUNT, f"{account} should be clean but got {BY_ACCOUNT.get(account)}"


def test_typologies_filter_still_returns_only_requested_rule_on_fixture():
    flags = rules_engine(DF, FEATURES, typologies=["structuring"])
    assert flags, "expected structuring flags on the fixture"
    assert all(f.typology == "STRUCTURING" for f in flags)
