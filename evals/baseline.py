"""evals/baseline.py — naive threshold-only baseline vs the full Caseline
hybrid pipeline, evaluated against labeled ground truth (IBM's own labels
plus the injected synthetic ring), measured ONCE on the held-out TEST split
(data/eval_splits/test.csv — see evals/split.py and METHODOLOGY.md; all
tuning decisions were made looking at the dev split only).

Reports, in the order a compliance team would actually ask for them:
1. The global table (flags, precision, recall, FPR) for both systems.
2. Per-tier metrics (HIGH, HIGH+MEDIUM, any-flag) — recall at any-flag is
   the fair comparison against the baseline's single undifferentiated
   output; HIGH is what "report" actually means operationally.
3. Precision@50 / Precision@100 — of the top N accounts by risk score,
   how many are truly involved in laundering. This is how alert triage
   capacity actually gets measured, not a global precision number nobody
   reviews to completion.
4. Pattern-level detection: of the 370 individual laundering ATTEMPTS
   documented in HI-Small_Patterns.txt (each BEGIN/END block — not just
   the coarser typology name, which would overstate coverage by letting
   one caught FAN-OUT attempt count for all FAN-OUT attempts), how many
   had at least one involved account flagged. Requires data/raw/ (skipped,
   not crashed, if absent). Plus explicit confirmation the injected
   synthetic ring was caught.
5. Alert volume in operational terms: flags per 1,000 accounts, and an
   estimated daily review load over the test split's own date range.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tools.anomaly_model import anomaly_model  # noqa: E402
from tools.feature_engine import feature_engine  # noqa: E402
from tools.graph_analysis import graph_analysis  # noqa: E402
from tools.risk_scorer import risk_scorer  # noqa: E402
from tools.rules_engine import rules_engine  # noqa: E402

BASELINE_THRESHOLD = 9_500.0
TEST_SPLIT_PATH = Path(__file__).resolve().parents[1] / "data" / "eval_splits" / "test.csv"
RAW_PATTERNS_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "HI-Small_Patterns.txt"
RING_MARK = "SMURFING (synthetic)"


def load_test_split() -> pd.DataFrame:
    df = pd.read_csv(TEST_SPLIT_PATH, parse_dates=["ts"])
    df["amount"] = df["amount"].astype(float)
    return df


def ground_truth_accounts(df: pd.DataFrame) -> set[str]:
    labeled = df[df.label == 1]
    return set(labeled.from_account) | set(labeled.to_account)


def baseline_flagged_accounts(df: pd.DataFrame) -> set[str]:
    big = df[df.amount >= BASELINE_THRESHOLD]
    return set(big.from_account) | set(big.to_account)


def run_caseline(df: pd.DataFrame):
    features = feature_engine(df)
    rule_flags = rules_engine(df, features)
    graph_flags = graph_analysis(df)
    scored = anomaly_model(features)
    return risk_scorer(rule_flags, graph_flags, scored)


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


def per_tier_metrics(records, truth: set[str], universe_size: int) -> dict[str, dict]:
    high = {r.account_id for r in records if r.risk_level == "HIGH"}
    high_medium = {r.account_id for r in records if r.risk_level in ("HIGH", "MEDIUM")}
    any_flag = {r.account_id for r in records}
    return {
        "HIGH only": compute_metrics(high, truth, universe_size),
        "HIGH+MEDIUM": compute_metrics(high_medium, truth, universe_size),
        "any flag (HIGH+MEDIUM+LOW)": compute_metrics(any_flag, truth, universe_size),
    }


def precision_at_n(records, truth: set[str], n: int) -> dict:
    top_n = records[:n]  # risk_scorer already returns records sorted by score desc
    hits = sum(1 for r in top_n if r.account_id in truth)
    return {"n": len(top_n), "hits": hits, "precision": hits / len(top_n) if top_n else 0.0}


def parse_attempt_groups(path: Path) -> list[tuple[str, set[str]]]:
    """Returns [(typology, {account_ids}), ...] — one entry per individual
    BEGIN/END attempt block (370 in the raw file), not collapsed by
    typology name. account_ids are formatted "bank-acct" to match the
    committed sample's schema."""
    groups: list[tuple[str, set[str]]] = []
    current_typology: str | None = None
    current_accounts: set[str] = set()
    for line in path.read_text().splitlines():
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            current_typology = line.split("-", 1)[1].strip().split(":", 1)[0].strip()
            current_accounts = set()
        elif line.startswith("END LAUNDERING ATTEMPT"):
            if current_typology is not None:
                groups.append((current_typology, current_accounts))
            current_typology = None
        elif current_typology and line.strip():
            f = line.split(",")
            if len(f) >= 5:
                current_accounts.add(f"{f[1]}-{f[2]}")
                current_accounts.add(f"{f[3]}-{f[4]}")
    return groups


def pattern_level_detection(flagged_any: set[str], test_universe: set[str]) -> dict | None:
    if not RAW_PATTERNS_PATH.exists():
        return None
    groups = parse_attempt_groups(RAW_PATTERNS_PATH)
    applicable = [(typ, accts & test_universe) for typ, accts in groups if accts & test_universe]
    detected = [g for g in applicable if g[1] & flagged_any]
    by_typology: dict[str, list[int]] = {}
    for typ, accts in applicable:
        hit = 1 if accts & flagged_any else 0
        by_typology.setdefault(typ, [0, 0])
        by_typology[typ][0] += hit
        by_typology[typ][1] += 1
    return {
        "total_attempts_in_raw_file": len(groups),
        "applicable_to_test_split": len(applicable),
        "detected": len(detected),
        "by_typology": by_typology,
    }


def operational_volume(records, df: pd.DataFrame, universe_size: int) -> dict:
    high_medium = [r for r in records if r.risk_level in ("HIGH", "MEDIUM")]
    span_days = max(1, (df.ts.max() - df.ts.min()).days)
    return {
        "total_candidates": len(records),
        "flags_per_1000_accounts": round(1000 * len(records) / universe_size, 2),
        "high_medium_count": len(high_medium),
        "high_medium_per_1000_accounts": round(1000 * len(high_medium) / universe_size, 2),
        "test_split_span_days": span_days,
        "estimated_high_medium_reviews_per_day": round(len(high_medium) / span_days, 1),
    }


def main() -> None:
    print(f"loading TEST split ({TEST_SPLIT_PATH}) and running both systems "
          "(baseline is instant; the hybrid pipeline fits IsolationForest once, ~2-6s)...\n")
    df = load_test_split()
    universe = set(df.from_account) | set(df.to_account)
    truth = ground_truth_accounts(df)
    ring_accounts = set(df[df.laundering_type == RING_MARK].from_account) | \
        set(df[df.laundering_type == RING_MARK].to_account)

    baseline_metrics = compute_metrics(baseline_flagged_accounts(df), truth, len(universe))
    records = run_caseline(df)
    agent_metrics = compute_metrics({r.account_id for r in records}, truth, len(universe))

    print(f"Ground truth: {len(truth):,} laundering-involved accounts out of {len(universe):,} total "
          f"(test split, {len(ring_accounts)} of them the injected synthetic ring)\n")

    print("=== 1. Global comparison ===")
    header = f"{'system':<26} {'flags':>8} {'precision':>10} {'recall':>8} {'FPR':>8}"
    print(header)
    print("-" * len(header))
    for name, m in [("naive threshold baseline", baseline_metrics), ("Caseline (hybrid)", agent_metrics)]:
        print(f"{name:<26} {m['flags']:>8,} {m['precision']:>10.1%} {m['recall']:>8.1%} {m['fpr']:>8.2%}")

    print("\n=== 2. Caseline per-tier metrics ===")
    tiers = per_tier_metrics(records, truth, len(universe))
    header2 = f"{'tier':<28} {'flags':>8} {'precision':>10} {'recall':>8} {'FPR':>8}"
    print(header2)
    print("-" * len(header2))
    for name, m in tiers.items():
        print(f"{name:<28} {m['flags']:>8,} {m['precision']:>10.1%} {m['recall']:>8.1%} {m['fpr']:>8.2%}")

    print("\n=== 3. Precision@N (alert triage capacity) ===")
    for n in (50, 100):
        p = precision_at_n(records, truth, n)
        print(f"Precision@{n}: {p['hits']}/{p['n']} = {p['precision']:.1%}")

    print("\n=== 4. Pattern-level detection ===")
    pattern_result = pattern_level_detection({r.account_id for r in records}, universe)
    if pattern_result is None:
        print("data/raw/HI-Small_Patterns.txt not present locally (gitignored Kaggle download) — skipped.")
    else:
        pr = pattern_result
        print(f"{pr['detected']}/{pr['applicable_to_test_split']} applicable laundering attempts "
              f"(of {pr['total_attempts_in_raw_file']} total in the raw file) had >=1 involved "
              "account flagged (any tier):")
        for typ, (hit, total) in sorted(pr["by_typology"].items()):
            print(f"  {typ:<15} {hit}/{total}")
    ring_flagged = ring_accounts & {r.account_id for r in records}
    print(f"\nInjected synthetic ring: {len(ring_flagged)}/{len(ring_accounts)} ring accounts flagged "
          f"({'CAUGHT' if '4521' in ring_flagged else 'MISSED'} — aggregator 4521 "
          f"{'is' if '4521' in ring_flagged else 'is NOT'} in the flagged set)")

    print("\n=== 5. Operational alert volume ===")
    vol = operational_volume(records, df, len(universe))
    print(f"Total candidates surfaced: {vol['total_candidates']:,} "
          f"({vol['flags_per_1000_accounts']} per 1,000 accounts)")
    print(f"HIGH+MEDIUM (\"worth a look\"): {vol['high_medium_count']:,} "
          f"({vol['high_medium_per_1000_accounts']} per 1,000 accounts)")
    print(f"Test split spans {vol['test_split_span_days']} days -> "
          f"~{vol['estimated_high_medium_reviews_per_day']} HIGH+MEDIUM reviews/day estimated")

    print("\n--- Markdown for README (section 1 only) ---\n")
    print("| System | Flags | Precision | Recall | False-Positive Rate |")
    print("|---|---|---|---|---|")
    for name, m in [("Naive threshold baseline", baseline_metrics), ("Caseline (hybrid)", agent_metrics)]:
        print(f"| {name} | {m['flags']:,} | {m['precision']:.1%} | {m['recall']:.1%} | {m['fpr']:.2%} |")


if __name__ == "__main__":
    main()
