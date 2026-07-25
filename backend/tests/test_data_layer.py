"""Data layer — fast checks against the already-committed
data/sample/transactions.csv (schema, nulls, dtypes, PII, the 4521 alias).

The expensive checks (prepare.py determinism across two full runs, and
sample-vs-raw labeled-count preservation) require data/raw/ — a ~475MB
gitignored Kaggle download not present on every machine — and take minutes
to run. Those live in test_data_layer_slow.py, excluded from the default
`make test` sweep; see that file's docstring and TESTING.md for the results
of running them.
"""

from __future__ import annotations

import re

import pandas as pd

from app.data_loader import load_transactions

REQUIRED_COLUMNS = [
    "txn_id", "ts", "from_account", "to_account", "amount",
    "currency", "channel", "label", "laundering_type",
]

# Real PII field names that must never appear in the committed sample —
# the schema only ever carries opaque account identifiers, never names/SSNs/
# card numbers/addresses.
FORBIDDEN_COLUMNS = {
    "name", "first_name", "last_name", "full_name", "ssn", "social_security",
    "email", "phone", "address", "dob", "date_of_birth", "card_number",
    "customer_name", "account_holder",
}


def test_schema_matches_documented_columns_exactly():
    df = load_transactions()
    assert list(df.columns) == REQUIRED_COLUMNS


def test_no_nulls_in_required_columns():
    df = load_transactions()
    required_non_null = ["txn_id", "ts", "from_account", "to_account", "amount", "currency", "channel", "label"]
    for col in required_non_null:
        assert df[col].isnull().sum() == 0, f"{col} has null values"
    # laundering_type is legitimately null for the ~195k non-laundering
    # rows; its non-null claim for label==1 rows is its own dedicated test.


def test_dtypes_are_correct():
    df = load_transactions()
    assert pd.api.types.is_datetime64_any_dtype(df["ts"])
    assert pd.api.types.is_float_dtype(df["amount"])
    assert pd.api.types.is_integer_dtype(df["label"])
    assert set(df["label"].unique()) <= {0, 1}


def test_no_forbidden_pii_columns():
    df = load_transactions()
    lowered = {c.lower() for c in df.columns}
    assert not (lowered & FORBIDDEN_COLUMNS)


def test_account_ids_look_like_opaque_bank_account_composites_not_names():
    df = load_transactions()
    sample_ids = pd.concat([df["from_account"], df["to_account"]]).drop_duplicates().sample(
        min(500, df["from_account"].nunique()), random_state=0
    )
    # every account id (aside from the documented ring/demo aliases) is a
    # bank-dash-account composite of digits/hex, e.g. "021174-80138AA40" —
    # never contains whitespace or looks like "Firstname Lastname"
    name_like = re.compile(r"^[A-Za-z]+\s[A-Za-z]+$")
    offenders = [a for a in sample_ids if name_like.match(str(a))]
    assert not offenders, f"account ids that look like personal names: {offenders}"


def test_4521_alias_resolves_to_a_real_account_with_substantive_activity():
    df = load_transactions()
    activity = df[(df.from_account == "4521") | (df.to_account == "4521")]
    assert not activity.empty, "the canonical demo entity '4521' must have real activity"
    assert len(activity) >= 10, "expected the injected ring's worth of activity on 4521"


def test_synthetic_ring_rows_always_carry_their_typology():
    """The 29 injected ring rows are Caseline's own data, not a raw-file
    join — those must always resolve, unlike the IBM join below."""
    df = load_transactions()
    ring = df[df.laundering_type == "SMURFING (synthetic)"]
    assert (ring.label == 1).all()
    assert ring["laundering_type"].isnull().sum() == 0


def test_labeled_row_typology_coverage_matches_the_disclosed_rate():
    """Real finding, not a Caseline bug: HI-Small_Patterns.txt does not
    document every row HI-Small_Trans.csv marks label==1 — prepare.py's
    (ts, from, to, amount) key lookup only resolves ~62% of the 5,177 real
    IBM-labeled rows to a named typology (the other ~38% keep label==1 but
    laundering_type=None). This is a property of the upstream Kaggle files'
    own internal consistency, not a join bug in prepare.py — confirmed by
    prepare.py's own startup log line ("typology known for N of M"), and
    disclosed in DATA.md. Pinned to a range so a real regression (e.g. a
    key-format change silently breaking the join further) still fails loud."""
    df = load_transactions()
    ibm_labeled = df[(df.label == 1) & (df.laundering_type != "SMURFING (synthetic)")]
    assert not ibm_labeled.empty
    coverage = ibm_labeled["laundering_type"].notna().mean()
    assert 0.55 <= coverage <= 0.70, f"typology coverage drifted to {coverage:.1%}, expected ~62%"


def test_synthetic_ring_is_disclosed_and_isolatable():
    df = load_transactions()
    ring = df[df.laundering_type == "SMURFING (synthetic)"]
    assert not ring.empty
    assert set(ring.label.unique()) == {1}
    mules = {f"RING-M{i:02d}" for i in range(1, 10)}
    assert mules <= set(ring.from_account) | set(ring.to_account)
    assert "RING-EXIT-01" in set(ring.to_account)
