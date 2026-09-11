from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.mark.integration
def test_live_release_requires_explicit_evidence_environment():
    """A CI/release job must opt into every live-readiness evidence class."""
    required = {
        "VESPER_DB_TESTS_PASSED",
        "VESPER_SDK_FIXTURES_PASSED",
        "VESPER_FAULT_TESTS_PASSED",
        "VESPER_CONTROLLED_ACCOUNT_PASSED",
        "VESPER_BACKUP_RESTORE_PASSED",
        "VESPER_EDA_TESTS_PASSED",
        "VESPER_EDA_REPLAY_PARITY_PASSED",
    }
    if os.getenv("VESPER_RELEASE_CANDIDATE") != "1":
        pytest.skip("release evidence gate is opt-in")
    missing = sorted(name for name in required if os.getenv(name) != "1")
    assert not missing, f"release evidence missing: {', '.join(missing)}"


def test_deployment_runbook_contains_no_profit_guarantee():
    text = Path(__file__).parents[2].joinpath("CONTROLLED_DEPLOYMENT.md").read_text(encoding="utf-8").lower()
    assert "no gate implies or guarantees future profit" in text
    assert "no-go" in text
