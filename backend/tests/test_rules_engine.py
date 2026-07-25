"""Unit tests proving the detection core catches:
1. the injected synthetic smurfing ring (aggregator account "4521"), and
2. a real IBM-labeled laundering account from the raw dataset.

Both run through the actual pipeline (filter_data -> feature_engine ->
rules_engine), not mocked inputs.
"""

from app.data_loader import load_transactions
from tools.feature_engine import feature_engine
from tools.filter_data import filter_data
from tools.rules_engine import rules_engine

# Ground truth established by inspecting data/sample/transactions.csv directly:
# a real (non-synthetic) IBM-labeled account with 6 near-threshold
# transactions inside a 7-day window. See DATA.md.
REAL_STRUCTURING_ACCOUNT = "0048309-811C599A0"


def _run_all_rules():
    df = load_transactions()
    filtered = filter_data(df, window_days=30)
    features = feature_engine(filtered)
    flags = rules_engine(filtered, features)
    return flags


def _flags_by_account(flags):
    by_account: dict[str, set[str]] = {}
    for f in flags:
        by_account.setdefault(f.account_id, set()).add(f.typology)
    return by_account


def test_injected_ring_aggregator_flagged_structuring_and_rapid_movement():
    by_account = _flags_by_account(_run_all_rules())
    assert "4521" in by_account, "aggregator account 4521 was not flagged at all"
    assert "STRUCTURING" in by_account["4521"], (
        "aggregator receives 27 sub-threshold deposits within 7 days — must trip STRUCTURING"
    )
    assert "RAPID_MOVEMENT" in by_account["4521"], (
        "aggregator moves ~85% of inbound funds out within 48h — must trip RAPID_MOVEMENT"
    )


def test_injected_ring_mule_flagged_structuring():
    by_account = _flags_by_account(_run_all_rules())
    assert "STRUCTURING" in by_account.get("RING-M01", set()), (
        "mule RING-M01 makes 3 sub-threshold deposits within 6 days — must trip STRUCTURING"
    )


def test_real_ibm_labeled_account_flagged_structuring():
    by_account = _flags_by_account(_run_all_rules())
    assert "STRUCTURING" in by_account.get(REAL_STRUCTURING_ACCOUNT, set()), (
        f"real labeled account {REAL_STRUCTURING_ACCOUNT} has 6 near-threshold txns in 7 days"
    )


def test_flags_are_a_small_minority_of_accounts():
    """Precision sanity check: rules should flag a small minority of
    accounts, not the majority — otherwise they're too loose to be useful."""
    df = load_transactions()
    filtered = filter_data(df, window_days=30)
    features = feature_engine(filtered)
    flags = rules_engine(filtered, features)

    flagged_accounts = {f.account_id for f in flags}
    total_accounts = len(features)
    assert total_accounts > 0
    assert len(flagged_accounts) < 0.05 * total_accounts, (
        f"{len(flagged_accounts)}/{total_accounts} accounts flagged — rules are too loose"
    )


def test_filter_data_window_is_relative_to_dataset_not_wallclock():
    df = load_transactions()
    filtered = filter_data(df, window_days=7)
    assert not filtered.empty
    assert filtered["ts"].max() == df["ts"].max()
    assert filtered["ts"].min() >= df["ts"].max() - __import__("pandas").Timedelta(days=7)


def test_rules_engine_typology_filter_runs_only_requested_rule():
    df = load_transactions()
    filtered = filter_data(df, window_days=30)
    features = feature_engine(filtered)
    flags = rules_engine(filtered, features, typologies=["rapid_movement"])
    assert flags, "expected at least one rapid_movement flag"
    assert all(f.typology == "RAPID_MOVEMENT" for f in flags)
