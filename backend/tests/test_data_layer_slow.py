"""Data layer — expensive checks that need data/raw/ (a ~475MB gitignored
Kaggle download, not present on every machine) and take tens of seconds:
prepare.py's determinism and its label-preservation claim against the raw
file, and inject_ring.py's determinism.

NOT part of the default `make test` sweep — excluded from pytest.ini's
collection by living outside the fast path is not automatic here, so this
file is invoked explicitly: `pytest backend/tests/test_data_layer_slow.py`.
Results from the run performed for this test pass are recorded in
TESTING.md (both checks passed: byte-identical reruns, exact label-count
match against the raw file).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"

pytestmark = pytest.mark.skipif(
    not (RAW_DIR / "HI-Small_Trans.csv").exists(),
    reason="data/raw/HI-Small_Trans.csv not present locally (gitignored Kaggle download)",
)


def _load_prepare_module():
    sys.path.insert(0, str(REPO_ROOT / "data"))
    if "prepare" in sys.modules:
        return importlib.reload(sys.modules["prepare"])
    return importlib.import_module("prepare")


def _run_prepare_into(tmp_out: Path) -> pd.DataFrame:
    prepare = _load_prepare_module()
    prepare.OUT = tmp_out
    prepare.main()
    return pd.read_csv(tmp_out / "transactions.csv")


def test_prepare_py_is_deterministic_across_two_runs(tmp_path):
    out1 = _run_prepare_into(tmp_path / "run1")
    out2 = _run_prepare_into(tmp_path / "run2")
    pd.testing.assert_frame_equal(out1, out2)


def test_prepare_py_preserves_every_labeled_row_from_raw(tmp_path):
    raw = pd.read_csv(RAW_DIR / "HI-Small_Trans.csv", dtype=str)
    raw_label_col = raw.columns[-1]
    raw_labeled_count = (raw[raw_label_col].astype(int) == 1).sum()

    out = _run_prepare_into(tmp_path / "run")
    sample_labeled_count = int((out["label"] == 1).sum())

    assert sample_labeled_count == raw_labeled_count, (
        f"sample has {sample_labeled_count} labeled rows, raw file has {raw_labeled_count} — "
        "the 'ALL labeled laundering transactions are preserved' claim in DATA.md must hold exactly"
    )


def test_inject_ring_py_is_deterministic_and_produces_the_documented_structure(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "data"))
    if "inject_ring" in sys.modules:
        inject_ring = importlib.reload(sys.modules["inject_ring"])
    else:
        inject_ring = importlib.import_module("inject_ring")

    base = _run_prepare_into(tmp_path / "base")
    sample_path = tmp_path / "sample.csv"
    base.to_csv(sample_path, index=False)

    inject_ring.SAMPLE = sample_path
    inject_ring.main()
    first = pd.read_csv(sample_path, parse_dates=["ts"])

    # re-run against the SAME already-injected file — idempotent by design
    inject_ring.main()
    second = pd.read_csv(sample_path, parse_dates=["ts"])
    pd.testing.assert_frame_equal(first, second)

    ring = first[first.laundering_type == "SMURFING (synthetic)"]
    mules = {f"RING-M{i:02d}" for i in range(1, 10)}
    assert mules <= set(ring.from_account)
    assert (ring.from_account.isin(mules) & (ring.to_account == "4521")).sum() == 27  # 9 mules x 3 deposits
    deposits = ring[ring.to_account == "4521"]
    assert (deposits.amount < 10_000).all() and (deposits.amount >= 9_000).all()
    assert set(ring[ring.from_account == "4521"].to_account) == {"RING-EXIT-01"}
