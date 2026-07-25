"""anomaly_model — IsolationForest anomaly score per account.

Fit ONCE against the full sample's feature matrix (fixed random_state=42, so
`make eval` output is byte-identical across machines) and cached at process
startup — see `warm_caches()` in app.main. Each query only calls
.decision_function() on its (filtered) subset, so every account's score is
comparable against the same population baseline and stays fast.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

RANDOM_STATE = 42
N_ESTIMATORS = 200

NUMERIC_COLUMNS = [
    "n_txns", "mean_amount", "std_amount",
    "hourly_count_mean", "hourly_count_std", "hourly_count_max",
    "near_threshold_count", "pct_near_threshold",
    "inbound_amount", "outbound_amount", "outbound_within_48h",
    "rapid_inout_ratio",
]


@lru_cache(maxsize=1)
def _baseline() -> tuple[IsolationForest, float, float]:
    """Fit once on the full committed sample's per-account features; also
    compute the population's median/p99 anomaly score so any subset's raw
    scores can be normalized onto a comparable [0,1] scale later."""
    # Imported lazily to avoid a circular import (feature_engine has no
    # dependency on anomaly_model, but app.data_loader does not import
    # tools — this keeps the dependency direction one-way and explicit).
    from app.data_loader import load_transactions
    from tools.feature_engine import feature_engine

    full_features = feature_engine(load_transactions())
    X = full_features[NUMERIC_COLUMNS].values
    model = IsolationForest(random_state=RANDOM_STATE, n_estimators=N_ESTIMATORS)
    model.fit(X)
    anomaly = -model.decision_function(X)  # flip: higher = more anomalous
    return model, float(np.median(anomaly)), float(np.percentile(anomaly, 99))


def anomaly_model(features: pd.DataFrame) -> pd.DataFrame:
    """Score accounts already present in `features` (a filtered subset)
    against the pre-fit population baseline. Higher score = more anomalous."""
    if features.empty:
        return features.assign(anomaly_score=pd.Series(dtype=float))
    model, _, _ = _baseline()
    raw = model.decision_function(features[NUMERIC_COLUMNS].values)  # higher = more normal
    out = features.copy()
    out["anomaly_score"] = -raw  # flip: higher = more anomalous
    return out


def normalize_anomaly_score(scores: pd.Series) -> pd.Series:
    """Map raw anomaly_score values onto [0,1] against the population
    median..p99 baseline (linear, clipped) — used by risk_scorer so the
    anomaly signal is comparable to the rules/graph components."""
    _, median, p99 = _baseline()
    if p99 <= median:
        return pd.Series(0.0, index=scores.index)
    return ((scores - median) / (p99 - median)).clip(lower=0.0, upper=1.0)
