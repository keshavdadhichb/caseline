"""anomaly_model — IsolationForest anomaly score per account.

Fit ONCE against the full sample's feature matrix (fixed random_state=42, so
`make eval` output is byte-identical across machines) and cached at process
startup — see `warm_caches()` in app.main. Each query only calls
.decision_function() on its (filtered) subset, so every account's score is
comparable against the same population baseline and stays fast.

`contamination` is deliberately left at sklearn's own default rather than
set to a measured value: on this pipeline it is a no-op regardless of what
it's set to. `IsolationForest.decision_function()` = score_samples() -
offset_, and `offset_` is derived from `contamination` at fit time — but
`normalize_anomaly_score()` below re-normalizes every raw score against the
population's own median and p99 of that SAME decision_function output. Any
constant shift `contamination` induces in `offset_` (and therefore in every
raw score) shifts the median and the p99 by exactly that same constant, so
it cancels out of `(score - median) / (p99 - median)` identically. Verified
empirically: contamination in {'auto', 0.1, 0.02, 0.005} all produced the
IDENTICAL count of accounts (12,060 of 150,971) crossing any given
normalized threshold. Setting contamination "to the measured base rate"
would look like a fix and change nothing — see METHODOLOGY.md. The
parameter that actually controls how many accounts the anomaly signal
alone can surface is ANOMALY_TOP_PERCENTILE below.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

RANDOM_STATE = 42
N_ESTIMATORS = 200

# The bar for "anomalous enough to matter without any rule or graph
# corroboration" (risk_scorer's LOW tier, and the "anomaly score above
# threshold" leg of its HIGH-tier corroboration check). Set from Step 0's
# measured base rate — 4.22% of accounts touch a labeled laundering
# transaction — rounded up to a clean top-5% cutoff rather than the exact
# 95.78th percentile, which would look precision-tuned it isn't. This
# ties the anomaly-alone bar to the population's own observed incidence
# rate instead of an arbitrary constant on an arbitrary [0,1] scale (the
# old ANOMALY_CANDIDATE_FLOOR=0.5, which let through ~8% of ALL accounts —
# nearly double the true base rate — on the least corroborated tier).
ANOMALY_TOP_PERCENTILE = 95.0

NUMERIC_COLUMNS = [
    "n_txns", "mean_amount", "std_amount",
    "hourly_count_mean", "hourly_count_std", "hourly_count_max",
    "near_threshold_count", "pct_near_threshold",
    "inbound_amount", "outbound_amount", "outbound_within_48h",
    "rapid_inout_ratio", "inbound_sender_count",
]


@lru_cache(maxsize=1)
def _baseline() -> tuple[IsolationForest, float, float, float]:
    """Fit once on the full committed sample's per-account features; also
    compute the population's median/p99/ANOMALY_TOP_PERCENTILE anomaly
    score so any subset's raw scores can be normalized and classified
    against a comparable, population-relative baseline later."""
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
    return (
        model,
        float(np.median(anomaly)),
        float(np.percentile(anomaly, 99)),
        float(np.percentile(anomaly, ANOMALY_TOP_PERCENTILE)),
    )


def anomaly_model(features: pd.DataFrame) -> pd.DataFrame:
    """Score accounts already present in `features` (a filtered subset)
    against the pre-fit population baseline. Higher score = more anomalous."""
    if features.empty:
        return features.assign(anomaly_score=pd.Series(dtype=float))
    model, _, _, _ = _baseline()
    raw = model.decision_function(features[NUMERIC_COLUMNS].values)  # higher = more normal
    out = features.copy()
    out["anomaly_score"] = -raw  # flip: higher = more anomalous
    return out


def normalize_anomaly_score(scores: pd.Series) -> pd.Series:
    """Map raw anomaly_score values onto [0,1] against the population
    median..p99 baseline (linear, clipped) — a continuous, human-readable
    figure for explanations and for ranking within/across risk tiers. Not
    used to decide tier membership — see is_anomaly_high for that."""
    _, median, p99, _ = _baseline()
    if p99 <= median:
        return pd.Series(0.0, index=scores.index)
    return ((scores - median) / (p99 - median)).clip(lower=0.0, upper=1.0)


def is_anomaly_high(scores: pd.Series) -> pd.Series:
    """True where a raw anomaly_score is in the population's top
    ANOMALY_TOP_PERCENTILE — the domain-justified (base-rate-derived) bar
    for "anomalous enough to count as a corroborating signal on its own",
    used by risk_scorer for both LOW-tier candidacy and HIGH tier's
    rule-plus-anomaly corroboration check."""
    _, _, _, top_percentile_value = _baseline()
    return scores >= top_percentile_value
