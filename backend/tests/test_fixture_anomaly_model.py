"""anomaly_model — determinism (fixed seed=42, refit twice), the
normalized-score range contract, and the single-row-frame edge case.
Scores a small fixture-derived feature subset against the REAL population
baseline (that's how every call site actually uses this tool — there is no
"toy" baseline to fit separately).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import tools.anomaly_model as anomaly_model_module
from tools.anomaly_model import anomaly_model, is_anomaly_high, normalize_anomaly_score
from tools.feature_engine import feature_engine
from tests.fixtures import build_fixture

FEATURES = feature_engine(build_fixture())


def test_baseline_refit_twice_is_byte_identical():
    anomaly_model_module._baseline.cache_clear()
    _, median1, p99_1, top1 = anomaly_model_module._baseline()
    anomaly_model_module._baseline.cache_clear()
    _, median2, p99_2, top2 = anomaly_model_module._baseline()
    assert median1 == median2
    assert p99_1 == p99_2
    assert top1 == top2


def test_scoring_the_same_subset_twice_after_refit_gives_identical_scores():
    anomaly_model_module._baseline.cache_clear()
    first = anomaly_model(FEATURES)["anomaly_score"].to_numpy()
    anomaly_model_module._baseline.cache_clear()
    second = anomaly_model(FEATURES)["anomaly_score"].to_numpy()
    np.testing.assert_array_equal(first, second)


def test_single_row_frame_does_not_raise():
    one_row = FEATURES.iloc[[0]]
    scored = anomaly_model(one_row)
    assert len(scored) == 1
    assert np.isfinite(scored["anomaly_score"].iloc[0])


def test_empty_frame_returns_empty_with_column():
    empty = feature_engine(pd.DataFrame(columns=["from_account", "to_account", "amount", "ts", "txn_id"]))
    scored = anomaly_model(empty)
    assert scored.empty
    assert "anomaly_score" in scored.columns


def test_normalized_score_is_always_within_zero_one():
    scored = anomaly_model(FEATURES)
    normalized = normalize_anomaly_score(scored.set_index("account_id")["anomaly_score"])
    assert (normalized >= 0.0).all()
    assert (normalized <= 1.0).all()


def test_normalize_handles_degenerate_population_without_dividing_by_zero(monkeypatch):
    """If p99 <= median (a degenerate/near-constant population), the
    function must return all-zero rather than divide by zero."""
    monkeypatch.setattr(anomaly_model_module, "_baseline", lambda: (None, 0.5, 0.5, 0.5))
    result = normalize_anomaly_score(pd.Series([0.1, 0.9], index=["X", "Y"]))
    assert (result == 0.0).all()


def test_is_anomaly_high_uses_the_top_percentile_not_the_normalized_floor(monkeypatch):
    monkeypatch.setattr(anomaly_model_module, "_baseline", lambda: (None, 0.0, 10.0, 8.0))
    result = is_anomaly_high(pd.Series([7.9, 8.0, 8.1, 100.0], index=["a", "b", "c", "d"]))
    assert result.tolist() == [False, True, True, True]


def test_anomaly_top_percentile_matches_step0_base_rate_order_of_magnitude():
    """Not a claim that 95.0 is derived by formula from 4.22% — it's a
    clean round number in the right neighborhood, documented in
    anomaly_model.py and METHODOLOGY.md. This just guards against someone
    quietly reverting it back toward the old, much-more-permissive
    ANOMALY_CANDIDATE_FLOOR-style behavior without noticing."""
    assert 90.0 <= anomaly_model_module.ANOMALY_TOP_PERCENTILE <= 98.0
