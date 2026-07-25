"""sar_drafter — LIVE LLM call (real network, real ANTHROPIC_API_KEY). Kept
separate from test_sar_drafter.py (which only exercises the template
fallback and is network-independent) because this one is slow and costs
real tokens; run explicitly, not part of the default fast loop judgment.

Verifies the 150-250 word contract and, critically, that every dollar
figure the narrative cites traces back to a number actually present in the
case file's own evidence/timeline (or a small set of generic regulatory
constants like the $10,000 CTR threshold) — not an invented amount.
"""

from __future__ import annotations

import re

import pytest

from tools.anomaly_model import anomaly_model
from tools.case_builder import build_indexes, case_builder
from tools.feature_engine import feature_engine
from tools.graph_analysis import graph_analysis
from tools.risk_scorer import risk_scorer
from tools.rules_engine import rules_engine
from tools.sar_drafter import draft_sar
from tests.fixtures import build_fixture

pytestmark = pytest.mark.live

# Generic regulatory/procedural figures a compliance narrative may legitimately
# cite without them appearing verbatim in this specific case's evidence.
DOMAIN_CONSTANTS = {10_000.0, 48.0, 24.0, 7.0, 30.0, 5313.0, 31.0}

# Money figures only: a leading $ or a trailing "dollars"/"percent" word.
# Deliberately NOT a bare-numeral regex — that would also catch date
# components (day-of-month, year) from narrative prose like "April 15,
# 2024", which are legitimate citations from case.timeline's timestamps
# but aren't dollar amounts and shouldn't be checked against the dollar
# allow-list.
DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*dollars\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*percent\b", re.IGNORECASE)

# The model isn't guaranteed to write numerals ("$12,000") — it sometimes
# spells amounts out in prose ("twelve thousand dollars"), observed live.
# A small closed-form word-number parser lets the fabrication check work
# either way instead of only catching numeral-formatted runs.
_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
_ALL_NUM_WORDS = set(_NUM_WORDS) | set(_SCALES)


def _words_to_number(tokens: list[str]) -> float | None:
    total, current, found = 0, 0, False
    for tok in tokens:
        if tok in _NUM_WORDS:
            current += _NUM_WORDS[tok]
            found = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
            found = True
    total += current
    return float(total) if found else None


def _word_number_phrases(text: str) -> list[float]:
    words = [w for token in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", text) for w in token.split("-")]
    lowered = [w.lower() for w in words]
    results, i, n = [], 0, len(lowered)
    while i < n:
        if lowered[i] in _ALL_NUM_WORDS:
            j = i
            while j < n and lowered[j] in _ALL_NUM_WORDS:
                j += 1
            val = _words_to_number(lowered[i:j])
            if val is not None:
                results.append(val)
            i = j
        else:
            i += 1
    return results


def _allowed_figures(case) -> set[float]:
    allowed: set[float] = set(DOMAIN_CONSTANTS)

    def _walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            allowed.add(round(float(value)))
            if 0.0 <= value <= 1.0:  # a ratio — also allow its percentage form ("92 percent")
                allowed.add(round(value * 100))
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _walk(v)

    for entry in case.evidence:
        _walk(entry)
    for row in case.timeline:
        _walk(row.get("amount"))
    return allowed


def _fanin_case():
    df = build_fixture()
    features = feature_engine(df)
    rule_flags = rules_engine(df, features)
    graph_flags = graph_analysis(df)
    scored = anomaly_model(features)
    records = risk_scorer(rule_flags, graph_flags, scored)
    record = next(r for r in records if r.account_id == "FANIN-AGG")
    idx = build_indexes(df, rule_flags, graph_flags)
    return case_builder(record, df, idx)


def test_live_sar_narrative_word_count_and_no_fabricated_dollar_amounts():
    case = _fanin_case()
    narrative = draft_sar(case)

    word_count = len(narrative.split())
    assert 150 <= word_count <= 250, f"expected 150-250 words, got {word_count}: {narrative!r}"

    allowed = _allowed_figures(case)
    numeral_cites = [
        round(float((a or b).replace(",", ""))) for a, b in DOLLAR_RE.findall(narrative)
    ] + [round(float(m.replace(",", ""))) for m in PERCENT_RE.findall(narrative)]
    word_cites = [round(v) for v in _word_number_phrases(narrative) if v >= 10]  # skip small counts/typology lists
    cited = numeral_cites + word_cites
    assert cited, "expected at least one numeric figure (numeral or spelled-out) cited in the narrative"
    fabricated = [c for c in cited if not any(abs(c - a) <= max(1, 0.01 * a) for a in allowed)]
    assert not fabricated, (
        f"narrative cites dollar figures not traceable to case evidence: {fabricated}\n"
        f"allowed (from case + domain constants): {sorted(allowed)}\n"
        f"narrative: {narrative}"
    )

    assert case.account_id in narrative, "must name the flagged account"

    # The model writes natural compliance prose ("fan-in aggregation",
    # "rapid fund movement"), not the raw typology tokens — check for
    # recognizable keywords per typology rather than an exact string match.
    typology_keywords = {
        "FAN_IN_RING": ("fan-in", "fan in", "aggregat", "consolidat"),
        "RAPID_MOVEMENT": ("rapid", "disburs", "moved out", "outbound"),
        "STRUCTURING": ("structur", "threshold"),
        "HIGH_RISK_AMOUNT": ("anomalous amount", "unusually large", "outlier"),
    }
    lowered = narrative.lower()
    for typ in case.typologies:
        keywords = typology_keywords.get(typ, ())
        assert any(k in lowered for k in keywords), f"expected a keyword for {typ} in: {narrative}"
