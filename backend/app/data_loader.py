"""Shared sample-dataset loader — single source of truth for dtypes/path."""

from functools import lru_cache
from pathlib import Path

import pandas as pd

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample" / "transactions.csv"


@lru_cache(maxsize=1)
def load_transactions() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE_PATH, parse_dates=["ts"])
    df["amount"] = df["amount"].astype(float)
    return df


@lru_cache(maxsize=1)
def known_accounts() -> frozenset[str]:
    """Every account id present in the sample, for validating an entity the
    planner extracted before running a query scoped to it."""
    df = load_transactions()
    return frozenset(df["from_account"]) | frozenset(df["to_account"])
