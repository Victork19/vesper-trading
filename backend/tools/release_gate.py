"""Conservative controlled-deployment evidence gate.

This script never enables live trading. It only reports whether independently
produced evidence markers are present. Missing evidence exits non-zero.
"""
from __future__ import annotations

import os
import sys


REQUIRED = (
    "VESPER_DB_TESTS_PASSED",
    "VESPER_SDK_FIXTURES_PASSED",
    "VESPER_FAULT_TESTS_PASSED",
    "VESPER_CONTROLLED_ACCOUNT_PASSED",
    "VESPER_BACKUP_RESTORE_PASSED",
)


def main() -> int:
    stage = os.getenv("VESPER_DEPLOYMENT_STAGE", "paper")
    hard_lock = os.getenv("VESPER_LIVE_HARD_LOCK", "true").lower()
    live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower()
    print(f"stage={stage} hard_lock={hard_lock} live_enabled={live_enabled}")
    missing = [name for name in REQUIRED if os.getenv(name) != "1"]
    if stage in {"controlled", "canary", "production"} and missing:
        print("NO-GO: missing evidence: " + ", ".join(missing))
        return 1
    if stage in {"canary", "production"} and (hard_lock != "false" or live_enabled != "true"):
        print("NO-GO: live stages require explicit hard-lock release and LIVE_TRADING_ENABLED=true")
        return 1
    print("GO for configured non-live stage; this script does not authorize capital deployment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
