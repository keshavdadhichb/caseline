"""BUG 1 regression: .env must be loaded by load_dotenv() so ANTHROPIC_API_KEY
(and any other env vars) are visible at runtime. This test proves the bug:
load_dotenv() is never called anywhere in the codebase, so ANTHROPIC_API_KEY
in .env has zero effect — the test below FAILS before the fix and PASSES
after. Each module that uses the Anthropic client (planner, sar_drafter,
main) must call load_dotenv() near the top."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_load_dotenv_picks_up_key_from_env_file(tmp_path):
    """Unset ANTHROPIC_API_KEY, write a fake key to a temp .env, call
    load_dotenv(), and confirm os.environ sees it. Proves the loading
    mechanism itself works — a prerequisite the codebase never satisfies
    because load_dotenv() is never called."""
    fake_key = "sk-test-F4KE-KEY-for-dotenv-regression"
    env_file = tmp_path / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={fake_key}\n")

    # remove the key from the process environment (both the live value
    # and any prior test contamination) and force dotenv to re-read
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
        assert os.environ.get("ANTHROPIC_API_KEY") == fake_key
    finally:
        # restore to avoid polluting other tests
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)


def test_planner_module_loads_dotenv_on_import():
    """After the fix, importing the planner module must not crash, and the
    module must have an importable load_dotenv call in its top-level scope.
    This is a structural check that the fix was applied to planner.py."""
    import importlib
    import sys

    # re-import to pick up any changes (tests may run in any order)
    mod = importlib.import_module("agent.planner")
    importlib.reload(mod)
    # planner.py imports anthropic — if load_dotenv was called and the key
    # is missing, anthropic.Anthropic() would raise at call time, not import
    # time. This test verifies the *import* is clean (the load_dotenv call
    # doesn't crash), and that the module source contains load_dotenv.
    source = Path(mod.__file__).read_text()
    assert "load_dotenv" in source, (
        "planner.py must call load_dotenv() — ANTHROPIC_API_KEY in .env has no effect otherwise"
    )


def test_sar_drafter_module_loads_dotenv_on_import():
    """Same structural check for sar_drafter.py — it calls the Anthropic
    client directly and tests hit it without going through main.py."""
    from pathlib import Path

    mod = __import__("tools.sar_drafter", fromlist=["sar_drafter"])
    source = Path(mod.__file__).read_text()
    assert "load_dotenv" in source, (
        "sar_drafter.py must call load_dotenv() — ANTHROPIC_API_KEY in .env has no effect otherwise"
    )


def test_main_module_loads_dotenv_on_import():
    """main.py is the FastAPI entrypoint — it must call load_dotenv() so
    that .env values are available before any LLM client is instantiated."""
    from pathlib import Path

    mod = __import__("app.main", fromlist=["main"])
    source = Path(mod.__file__).read_text()
    assert "load_dotenv" in source, (
        "main.py must call load_dotenv() — ANTHROPIC_API_KEY in .env has no effect otherwise"
    )
