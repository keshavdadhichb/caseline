"""aggregate_data — plain counting over accounts, with no risk judgement.

Some questions are factual rather than investigative: "which customers made
10+ transactions under $10,000?" wants a count and a list, not a typology.
Before this tool existed the planner had nothing that could answer it and
mapped it onto the structuring rule instead — a different band ($9,000 to
$10,000 rather than anything under $10,000), a different count (3 vs 10)
and a different window, which returned 132 accounts when the true answer
was 1,002. A plausible-looking answer to a question nobody asked.

The output deliberately carries no risk level. Matching a count is not
evidence of wrongdoing, and tiering these accounts would overstate what was
actually found.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_TOP_N = 200


def aggregate_data(
    df: pd.DataFrame,
    min_count: int | None = None,
    max_amount: float | None = None,
    min_amount: float | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Count each account's transactions inside an amount band, then keep the
    accounts meeting `min_count`. An account counts on either side of a
    transaction, matching how "customers who made N transactions" reads."""
    if df.empty:
        return {"criteria": {}, "matched": 0, "rows": [], "truncated": False}

    scoped = df
    if max_amount is not None:
        scoped = scoped[scoped["amount"] < max_amount]
    if min_amount is not None:
        scoped = scoped[scoped["amount"] >= min_amount]

    counts = pd.concat([scoped["from_account"], scoped["to_account"]]).value_counts()
    totals = (
        pd.concat([
            scoped[["from_account", "amount"]].rename(columns={"from_account": "account_id"}),
            scoped[["to_account", "amount"]].rename(columns={"to_account": "account_id"}),
        ])
        .groupby("account_id")["amount"].sum()
    )

    if min_count is not None:
        counts = counts[counts >= min_count]

    ranked = counts.sort_values(ascending=False)
    rows = [
        {
            "account_id": str(acct),
            "count": int(n),
            "total_amount": round(float(totals.get(acct, 0.0)), 2),
        }
        for acct, n in ranked.head(top_n).items()
    ]
    return {
        "criteria": {"min_count": min_count, "max_amount": max_amount, "min_amount": min_amount},
        "matched": int(len(ranked)),
        "rows": rows,
        "truncated": bool(len(ranked) > len(rows)),
    }
