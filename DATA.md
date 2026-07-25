# Caseline — Data Notes

## Source

- **IBM Transactions for Anti-Money Laundering (AML)** — HI-Small variant.
  Kaggle: `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`
  (Community Data License Agreement — Sharing 1.0). Raw files live in
  `data/raw/` (gitignored — do not commit or redistribute raw data).
- Backup dataset (unused unless IBM fails): SAML-D
  (`berkanoztas/synthetic-transaction-monitoring-dataset-aml`).

## Committed sample — `data/sample/transactions.csv`

Produced by `make data` (`data/prepare.py` then `data/inject_ring.py`),
deterministic with fixed seeds (42 / 7).

- ~200k rows sampled from 5.08M raw transactions.
- **ALL labeled laundering transactions are preserved**, plus up to 80k
  transactions touching laundering counterparty accounts, plus a random fill.
- `laundering_type` is parsed from `HI-Small_Patterns.txt` (FAN-OUT, FAN-IN,
  CYCLE, GATHER-SCATTER, STACK, BIPARTITE, RANDOM) by matching each labeled
  row's (timestamp, from, to, amount) against the patterns file. **Coverage
  is ~62% of the 5,177 real IBM-labeled rows** — the patterns file does not
  document every transaction the trans file marks `label=1`; the remainder
  keep `label=1` with `laundering_type=None`. This is a property of the
  upstream Kaggle files, not a Caseline join bug (see
  `backend/tests/test_data_layer.py`). The 29 synthetic ring rows always
  carry their typology (`SMURFING (synthetic)`) since they're Caseline's
  own data, not a join.
- `amount` is **normalized to USD** using fixed Sep-2022 reference rates
  (see `USD_RATES` in `data/prepare.py`); the original currency is kept in
  the `currency` column. All thresholds in Caseline are USD ($10,000
  reporting threshold).
- Account IDs are `bank-account` composites (account numbers alone are not
  unique across banks).

## Synthetic ring (disclosed)

`data/inject_ring.py` injects **one clearly-marked synthetic smurfing ring**
(`laundering_type = "SMURFING (synthetic)"`, txn ids `S0000...`):

- 9 mule accounts (`RING-M01..RING-M09`) each make 3 deposits of
  $9,050–$9,950 (just under the $10,000 threshold) into aggregator account
  **`4521`** over 6 days near the end of the sample window.
- The aggregator then moves ~85% of the funds to `RING-EXIT-01` within 48h
  (rapid movement).

**Entity alias:** account `4521` is the canonical demo entity — queries about
"customer ID 4521" resolve to this aggregator account.

## Timestamps

Raw data is from Sept 2022, so relative windows ("last 30 days") are computed
against the **dataset's max timestamp**, not the wall clock.
