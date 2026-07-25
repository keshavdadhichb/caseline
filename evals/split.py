"""evals/split.py — deterministic dev/test split for detection tuning.

Splits by ACCOUNT, not by transaction row. Detection rules are inherently
relational: STRUCTURING needs an account's full 7-day window of deposits,
FAN_IN_RING needs a receiver's full set of senders. A naive random split of
transaction ROWS would scatter a single account's pattern across both
halves — e.g. 3 of a structuring account's 5 near-threshold deposits landing
in dev and 2 in test — which would silently degrade detection in BOTH
splits as an artifact of the split itself, not a real signal about rule
quality. That artifact would be indistinguishable from "the tightened
rules are too strict" while tuning, which is exactly the failure mode this
whole exercise is trying to avoid.

So: every ACCOUNT is assigned to exactly one of {dev, test}, stratified on
whether the account touches any labeled laundering transaction (so both
splits get a proportional share of positives), with the injected ring's
accounts force-assigned to test per the task's requirement. A transaction
then belongs to a split if EITHER endpoint is assigned to it — a
transaction whose two accounts land in different splits appears in both
(each account needs its own full activity to compute correct features),
so dev_rows + test_rows can exceed the original row count. What's
invariant is that any given account's transaction history is never split
across dev and test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.data_loader import load_transactions  # noqa: E402

SEED = 42
DEV_FRACTION = 0.40
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "eval_splits"

RING_MARK = "SMURFING (synthetic)"


def ring_accounts(df: pd.DataFrame) -> set[str]:
    ring = df[df.laundering_type == RING_MARK]
    return set(ring.from_account) | set(ring.to_account)


def split_accounts(df: pd.DataFrame) -> tuple[set[str], set[str]]:
    rng = np.random.default_rng(SEED)

    labeled = df[df.label == 1]
    positive_accounts = set(labeled.from_account) | set(labeled.to_account)
    all_accounts = set(df.from_account) | set(df.to_account)
    forced_test = ring_accounts(df)

    # stratify: split positives and negatives (excluding forced-test ring
    # accounts, which never enter the random split at all) independently,
    # each at the same dev fraction, then union.
    def _split(accounts: set[str]) -> tuple[set[str], set[str]]:
        arr = np.array(sorted(accounts))  # sorted first: deterministic order before shuffling
        rng.shuffle(arr)
        cut = int(len(arr) * DEV_FRACTION)
        return set(arr[:cut]), set(arr[cut:])

    positive_pool = positive_accounts - forced_test
    negative_pool = all_accounts - positive_accounts - forced_test

    dev_pos, test_pos = _split(positive_pool)
    dev_neg, test_neg = _split(negative_pool)

    dev_accounts = dev_pos | dev_neg
    test_accounts = test_pos | test_neg | forced_test
    return dev_accounts, test_accounts


def build_split_frame(df: pd.DataFrame, accounts: set[str]) -> pd.DataFrame:
    mask = df.from_account.isin(accounts) | df.to_account.isin(accounts)
    return df[mask].reset_index(drop=True)


def main() -> None:
    df = load_transactions()
    dev_accounts, test_accounts = split_accounts(df)

    assert dev_accounts.isdisjoint(test_accounts), "an account must never appear in both splits"
    forced = ring_accounts(df)
    assert forced <= test_accounts, "every ring account must be in test"
    assert forced.isdisjoint(dev_accounts), "no ring account may leak into dev"

    dev_df = build_split_frame(df, dev_accounts)
    test_df = build_split_frame(df, test_accounts)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dev_df.to_csv(OUT_DIR / "dev.csv", index=False)
    test_df.to_csv(OUT_DIR / "test.csv", index=False)

    def _report(name: str, accounts: set[str], frame: pd.DataFrame) -> None:
        labeled = frame[frame.label == 1]
        pos_accounts = (set(labeled.from_account) | set(labeled.to_account)) & accounts
        print(f"{name:>5}: {len(accounts):>7,} accounts | {len(frame):>7,} rows | "
              f"{len(labeled):>5,} labeled rows | {len(pos_accounts):>5,} positive accounts "
              f"({100 * len(pos_accounts) / len(accounts):.2f}%)")

    print(f"seed={SEED}  dev_fraction={DEV_FRACTION}")
    _report("dev", dev_accounts, dev_df)
    _report("test", test_accounts, test_df)
    print(f"ring accounts ({len(forced)}) confirmed test-only: "
          f"{sorted(forced) == sorted(forced & test_accounts)}")


if __name__ == "__main__":
    main()
