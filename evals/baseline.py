"""evals/baseline.py — naive threshold-only baseline vs the full Caseline
hybrid pipeline, evaluated against labeled ground truth (IBM's own labels
plus the injected synthetic ring). Prints flag counts, precision, recall,
and false-positive rate for both, at the account level. This table goes
verbatim into the README.

The baseline is deliberately crude: flag any account touching a single
transaction above a fixed dollar threshold. No rolling windows, no
rate-awareness, no network analysis — the kind of rule a first pass at
"AML detection" would ship with, and the natural point of comparison for
"rules give precision, the model catches what rules miss, the graph
catches networks" (CLAUDE.md's hybrid-scoring thesis).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.data_loader import load_transactions  # noqa: E402
from tools.anomaly_model import anomaly_model  # noqa: E402
from tools.feature_engine import feature_engine  # noqa: E402
from tools.graph_analysis import graph_analysis  # noqa: E402
from tools.risk_scorer import risk_scorer  # noqa: E402
from tools.rules_engine import rules_engine  # noqa: E402

BASELINE_THRESHOLD = 9_500.0


def ground_truth_accounts(df) -> set[str]:
    labeled = df[df.label == 1]
    return set(labeled.from_account) | set(labeled.to_account)


def baseline_flagged_accounts(df) -> set[str]:
    big = df[df.amount >= BASELINE_THRESHOLD]
    return set(big.from_account) | set(big.to_account)


def agent_flagged_accounts(df) -> set[str]:
    features = feature_engine(df)
    rule_flags = rules_engine(df, features)
    graph_flags = graph_analysis(df)
    scored = anomaly_model(features)
    records = risk_scorer(rule_flags, graph_flags, scored)
    return {r.account_id for r in records}


def compute_metrics(flagged: set[str], truth: set[str], universe_size: int) -> dict:
    tp = len(flagged & truth)
    fp = len(flagged - truth)
    fn = len(truth - flagged)
    tn = universe_size - tp - fp - fn
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "flags": len(flagged), "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "fpr": fpr,
    }


def main() -> None:
    print("loading dataset and running both systems (baseline is instant; "
          "the hybrid pipeline fits IsolationForest once, ~2-6s)...\n")
    df = load_transactions()
    universe = set(df.from_account) | set(df.to_account)
    truth = ground_truth_accounts(df)

    baseline = compute_metrics(baseline_flagged_accounts(df), truth, len(universe))
    agent = compute_metrics(agent_flagged_accounts(df), truth, len(universe))

    print(f"Ground truth: {len(truth):,} laundering-involved accounts out of {len(universe):,} total\n")
    header = f"{'system':<26} {'flags':>8} {'precision':>10} {'recall':>8} {'FPR':>8}"
    print(header)
    print("-" * len(header))
    for name, m in [("naive threshold baseline", baseline), ("Caseline (hybrid)", agent)]:
        print(f"{name:<26} {m['flags']:>8,} {m['precision']:>10.1%} {m['recall']:>8.1%} {m['fpr']:>8.2%}")

    print("\n--- Markdown for README ---\n")
    print("| System | Flags | Precision | Recall | False-Positive Rate |")
    print("|---|---|---|---|---|")
    for name, m in [("Naive threshold baseline", baseline), ("Caseline (hybrid)", agent)]:
        print(f"| {name} | {m['flags']:,} | {m['precision']:.1%} | {m['recall']:.1%} | {m['fpr']:.2%} |")


if __name__ == "__main__":
    main()
