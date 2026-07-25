"""evals/baseline.py — independently verify compute_metrics' precision/
recall/false-positive-rate arithmetic on hand-built examples (not by
re-deriving its own formula, by checking against numbers worked out by
hand) before trusting the numbers it prints into the README.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from baseline import compute_metrics  # noqa: E402


def test_compute_metrics_matches_hand_worked_example():
    # truth={A,B,C,D}, flagged={A,B,E,F}: 2 true positives (A,B), 2 false
    # positives (E,F), 2 false negatives (C,D), universe of 10 -> 4 true
    # negatives (G,H,I,J).
    m = compute_metrics(flagged={"A", "B", "E", "F"}, truth={"A", "B", "C", "D"}, universe_size=10)
    assert m == {
        "flags": 4, "tp": 2, "fp": 2, "fn": 2,
        "precision": 0.5, "recall": 0.5, "fpr": 2 / 6,
    }


def test_compute_metrics_no_flags_at_all():
    m = compute_metrics(flagged=set(), truth={"A"}, universe_size=5)
    assert m["precision"] == 0.0  # guarded 0/0
    assert m["recall"] == 0.0
    assert m["fpr"] == 0.0


def test_compute_metrics_all_flags_are_false_positives():
    m = compute_metrics(flagged={"A"}, truth=set(), universe_size=5)
    assert m["tp"] == 0 and m["fp"] == 1
    assert m["precision"] == 0.0
    assert m["fpr"] == 0.2  # 1 fp / (1 fp + 4 tn)


def test_compute_metrics_perfect_match_has_no_false_positive_rate_guard_issue():
    m = compute_metrics(flagged={"A", "B"}, truth={"A", "B"}, universe_size=2)
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["fpr"] == 0.0  # guarded 0/0 (fp=0, tn=0)
