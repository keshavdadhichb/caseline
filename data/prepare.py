"""Normalize the IBM AML (HI-Small) dataset into the Caseline sample.

Input  (gitignored): data/raw/HI-Small_Trans.csv, data/raw/HI-Small_Patterns.txt
Output (committed):  data/sample/transactions.csv  (~200k rows)

Schema: txn_id, ts, from_account, to_account, amount, currency, channel,
        label, laundering_type

Sampling (seed 42): ALL labeled laundering transactions are preserved, plus a
capped sample of transactions touching laundering counterparty accounts, plus
a random fill to TARGET_ROWS. `amount` is normalized to USD with the fixed
reference rates below (Sep-2022 approximations, cited in DATA.md); the
original currency is retained in `currency`.
"""

from pathlib import Path

import pandas as pd

SEED = 42
TARGET_ROWS = 200_000
CONTEXT_CAP = 80_000

RAW = Path(__file__).parent / "raw"
OUT = Path(__file__).parent / "sample"

# Fixed USD reference rates (approx. Sep 2022) — documented in DATA.md.
USD_RATES = {
    "US Dollar": 1.0,
    "Euro": 1.00,
    "Yuan": 0.145,
    "Yen": 0.0070,
    "UK Pound": 1.15,
    "Rupee": 0.0125,
    "Ruble": 0.0165,
    "Canadian Dollar": 0.73,
    "Australian Dollar": 0.65,
    "Swiss Franc": 1.02,
    "Mexican Peso": 0.050,
    "Shekel": 0.29,
    "Saudi Riyal": 0.266,
    "Brazil Real": 0.19,
    "Bitcoin": 20_000.0,
}


def parse_patterns(path: Path) -> dict[tuple, str]:
    """Map (ts, from_bank, from_acct, to_bank, to_acct, amount_paid) -> typology."""
    mapping: dict[tuple, str] = {}
    current = None
    for line in path.read_text().splitlines():
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            tail = line.split("-", 1)[1].strip()
            current = tail.split(":", 1)[0].strip()
        elif line.startswith("END LAUNDERING ATTEMPT"):
            current = None
        elif current and line.strip():
            f = line.split(",")
            if len(f) >= 9:
                mapping[(f[0], f[1], f[2], f[3], f[4], f[7])] = current
    return mapping


def main() -> None:
    print("reading raw transactions (5.1M rows, ~1 min)...")
    df = pd.read_csv(RAW / "HI-Small_Trans.csv", dtype=str)
    df.columns = [
        "ts_raw", "from_bank", "from_acct", "to_bank", "to_acct",
        "amount_received", "recv_currency", "amount_paid", "currency",
        "channel", "label",
    ]

    print("tagging laundering typologies from patterns file...")
    patterns = parse_patterns(RAW / "HI-Small_Patterns.txt")
    keys = list(zip(df.ts_raw, df.from_bank, df.from_acct,
                    df.to_bank, df.to_acct, df.amount_paid))
    df["laundering_type"] = [patterns.get(k) for k in keys]
    df["label"] = df["label"].astype(int)

    n_laundering = int((df.label == 1).sum())
    print(f"labeled laundering rows: {n_laundering:,} "
          f"(typology known for {df.laundering_type.notna().sum():,})")

    laundering = df[df.label == 1]
    ring_accounts = set(laundering.from_bank + "-" + laundering.from_acct) | \
                    set(laundering.to_bank + "-" + laundering.to_acct)

    from_id = df.from_bank + "-" + df.from_acct
    to_id = df.to_bank + "-" + df.to_acct
    touches = (from_id.isin(ring_accounts) | to_id.isin(ring_accounts)) & (df.label == 0)
    context = df[touches]
    if len(context) > CONTEXT_CAP:
        context = context.sample(CONTEXT_CAP, random_state=SEED)

    rest = df[~df.index.isin(laundering.index) & ~df.index.isin(context.index)]
    fill_n = max(0, TARGET_ROWS - len(laundering) - len(context))
    fill = rest.sample(min(fill_n, len(rest)), random_state=SEED)

    sample = pd.concat([laundering, context, fill])
    sample["from_account"] = sample.from_bank + "-" + sample.from_acct
    sample["to_account"] = sample.to_bank + "-" + sample.to_acct
    sample["ts"] = pd.to_datetime(sample.ts_raw, format="%Y/%m/%d %H:%M")

    rate = sample.currency.map(USD_RATES).fillna(1.0)
    sample["amount"] = (sample.amount_paid.astype(float) * rate).round(2)

    # kind="mergesort" (stable): the default quicksort doesn't preserve
    # input order for tied timestamps (minute-granularity ts means many
    # ties at 200k rows), so a fresh `make data` run could land same-minute
    # transactions in a different relative order than a previous run even
    # with identical fixed seeds — and since IsolationForest's seeded fit
    # depends on row position, that could shift anomaly scores between
    # runs. A stable sort makes tie order a pure function of the
    # (seed-deterministic) pre-sort order.
    sample = sample.sort_values("ts", kind="mergesort").reset_index(drop=True)
    sample["txn_id"] = ["T%06d" % i for i in range(len(sample))]

    out = sample[["txn_id", "ts", "from_account", "to_account", "amount",
                  "currency", "channel", "label", "laundering_type"]]
    OUT.mkdir(exist_ok=True)
    out.to_csv(OUT / "transactions.csv", index=False)
    print(f"wrote {len(out):,} rows -> {OUT / 'transactions.csv'} "
          f"({(OUT / 'transactions.csv').stat().st_size / 1e6:.1f} MB)")
    print(f"  laundering: {int(out.label.sum()):,} | window: "
          f"{out.ts.min()} .. {out.ts.max()}")


if __name__ == "__main__":
    main()
