"""BUG 2 regression: CLAUDE.md and test_sar_drafter.py use a stale bare
"STRUCTURING" string that no longer exists as a typology in rules_engine.py.
The rules engine now has STRUCTURING_HIGH and STRUCTURING_MEDIUM; "STRUCTURING"
matches neither. This test proves the mismatch exists."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def _read(relpath: str) -> str:
    return (BACKEND_DIR / relpath).read_text()


def _rule_engine_typologies() -> set[str]:
    """Extract the actual typology string literals emitted by rules_engine."""
    source = _read("tools/rules_engine.py")
    return set(re.findall(r'typology="([A-Z_]+)"', source))


def test_rules_engine_has_no_bare_structuring_typology():
    """rules_engine.py must NOT emit a bare "STRUCTURING" — it must be
    STRUCTURING_HIGH or STRUCTURING_MEDIUM."""
    typologies = _rule_engine_typologies()
    assert "STRUCTURING" not in typologies, (
        "bare 'STRUCTURING' should not appear as a typology — "
        "rules_engine emits STRUCTURING_HIGH and STRUCTURING_MEDIUM"
    )
    assert "STRUCTURING_HIGH" in typologies
    assert "STRUCTURING_MEDIUM" in typologies


def test_stale_structuring_literal_in_sar_drafter():
    """test_sar_drafter.py's fixture uses 'STRUCTURING' as a typology string
    — this must match what the rules engine actually emits."""
    fixture_source = _read("tests/test_sar_drafter.py")
    typology_refs = re.findall(r'"(STRUCTURING[A-Z_]*)"', fixture_source)
    bare = [t for t in typology_refs if t == "STRUCTURING"]
    assert not bare, (
        "test_sar_drafter.py fixture uses bare 'STRUCTURING' — "
        "must be STRUCTURING_HIGH or STRUCTURING_MEDIUM to match rules_engine"
    )


def test_stale_structuring_literal_in_e2e_live():
    """test_e2e_live.py's assertion checks for 'STRUCTURING' in rules_fired
    — the rules engine emits STRUCTURING_HIGH, not bare STRUCTURING."""
    fixture_source = _read("tests/test_e2e_live.py")
    assert '"STRUCTURING"' not in fixture_source or '"STRUCTURING_HIGH"' in fixture_source, (
        "test_e2e_live.py uses bare 'STRUCTURING' — must be STRUCTURING_HIGH"
    )
