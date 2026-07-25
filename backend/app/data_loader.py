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
