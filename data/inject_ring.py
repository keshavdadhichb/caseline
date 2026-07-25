"""Inject ONE documented SYNTHETIC smurfing ring into the committed sample.

SYNTHETIC DATA — clearly marked. 9 mule accounts each deposit amounts just
under the $10,000 reporting threshold into aggregator account "4521" over 6
days; the aggregator then moves ~85% out within 48h (rapid movement). This
guarantees a narratable catch for the demo and is disclosed in DATA.md and
the README. Fixed seed => byte-identical output across machines.

Idempotent: re-running replaces any previously injected ring rows.
"""

import random
from datetime import timedelta
from pathlib import Path

import pandas as pd

SEED = 7
SAMPLE = Path(__file__).parent / "sample" / "transactions.csv"

AGGREGATOR = "4521"           # canonical demo entity ("customer ID 4521")
MULES = [f"RING-M{i:02d}" for i in range(1, 10)]
EXIT_ACCOUNT = "RING-EXIT-01"
MARK = "SMURFING (synthetic)"


def main() -> None:
    rng = random.Random(SEED)
    df = pd.read_csv(SAMPLE, parse_dates=["ts"])
    df = df[df.laundering_type != MARK]  # idempotency

    end = df.ts.max().normalize() - timedelta(days=1)
    start = end - timedelta(days=6)

    rows = []
    total = 0.0
    for mule in MULES:
        for _ in range(3):
            amt = round(rng.uniform(9_050, 9_950), 2)  # within 10% below $10k
            ts = start + timedelta(minutes=rng.randint(0, 6 * 24 * 60))
            total += amt
            rows.append((ts, mule, AGGREGATOR, amt))

    # consolidation: ~85% moved out within 48h of the last deposit
    last_dep = max(r[0] for r in rows)
    out_total = round(total * 0.85, 2)
    split = round(out_total * rng.uniform(0.4, 0.6), 2)
    rows.append((last_dep + timedelta(hours=rng.randint(4, 24)),
                 AGGREGATOR, EXIT_ACCOUNT, split))
    rows.append((last_dep + timedelta(hours=rng.randint(25, 47)),
                 AGGREGATOR, EXIT_ACCOUNT, round(out_total - split, 2)))

    ring = pd.DataFrame(rows, columns=["ts", "from_account", "to_account", "amount"])
    ring["txn_id"] = ["S%04d" % i for i in range(len(ring))]
    ring["currency"] = "US Dollar"
    ring["channel"] = "ACH"
    ring["label"] = 1
    ring["laundering_type"] = MARK

    out = pd.concat([df, ring[df.columns]]).sort_values("ts").reset_index(drop=True)
    out.to_csv(SAMPLE, index=False)
    print(f"injected {len(ring)} synthetic ring txns "
          f"(${total:,.0f} into {AGGREGATOR} from {len(MULES)} mules, "
          f"{start.date()}..{end.date()}); sample now {len(out):,} rows")


if __name__ == "__main__":
    main()
