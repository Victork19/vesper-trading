from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def test_migrations_are_numbered_and_unique():
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, "no migrations found"
    versions = [re.match(r"^(\d+)_", item.name).group(1) for item in files]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)


def test_execution_integrity_schema_has_append_only_triggers():
    sql = (MIGRATIONS / "0006_execution_integrity.sql").read_text(encoding="utf-8").lower()
    assert "execution_fills_no_update" in sql
    assert "execution_ledger_no_update" in sql
    assert "before update or delete" in sql


def test_financial_ledger_has_signed_realized_pnl_dimension():
    """Release gate: signed PnL must not be stored in non-negative notional."""
    sql = "\n".join(item.read_text(encoding="utf-8") for item in MIGRATIONS).lower()
    has_signed_dimension = any(
        field in sql for field in ("realized_pnl", "cash_delta", "signed_amount")
    )
    assert has_signed_dimension, (
        "ledger requires a signed realized-PnL/cash dimension; non-negative "
        "notional cannot represent losses"
    )


@pytest.mark.integration
def test_database_schema_is_current(database_schema):
    expected = sorted(item.stem for item in MIGRATIONS.glob("*.sql"))
    assert database_schema == expected
