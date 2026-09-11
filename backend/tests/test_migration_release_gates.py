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


def test_eda_schema_has_append_only_triggers_and_replay_storage():
    sql = (MIGRATIONS / "0010_eda_foundation.sql").read_text(encoding="utf-8").lower()
    experiential = (MIGRATIONS / "0011_eda_experiential.sql").read_text(encoding="utf-8").lower()
    assert "eda_episodes_no_update" in sql
    assert "eda_events_no_update" in sql
    assert "before update or delete" in sql
    assert "eda_replay_runs" in experiential
    assert "eda_model_registry" in experiential
    assert "eda_opportunities" in experiential


def test_financial_ledger_has_signed_realized_pnl_dimension():
    """Release gate: signed PnL must not be stored in non-negative notional."""
    sql = "\n".join(item.read_text(encoding="utf-8") for item in MIGRATIONS.glob("*.sql")).lower()
    has_signed_dimension = any(
        field in sql for field in ("realized_pnl", "cash_delta", "signed_amount")
    )
    assert has_signed_dimension, (
        "ledger requires a signed realized-PnL/cash dimension; non-negative "
        "notional cannot represent losses"
    )


@pytest.mark.integration
def test_eda_episode_and_event_history_is_append_only(pg_connection):
    pg_connection.execute("""INSERT INTO eda_episodes
        (episode_id,decision_id,created_at,timestamp_decision,episode,episode_hash,schema_version)
        VALUES('test-eda-episode','test-eda-decision',NOW(),NOW(),'{}','test-eda-hash','eda_episode_v1')""")
    pg_connection.execute("""INSERT INTO eda_events
        (event_id,episode_id,source,event_type,event_time,ingestion_time,payload,schema_version,quality)
        VALUES('test-eda-event','test-eda-episode','test','test',NOW(),NOW(),'{}','eda_event_v1','{}')""")
    with pytest.raises(Exception):
        pg_connection.execute("UPDATE eda_episodes SET schema_version='tampered' WHERE episode_id='test-eda-episode'")
    with pytest.raises(Exception):
        pg_connection.execute("DELETE FROM eda_events WHERE event_id='test-eda-event'")


@pytest.mark.integration
def test_database_schema_is_current(database_schema):
    expected = sorted(item.stem for item in MIGRATIONS.glob("*.sql"))
    assert database_schema == expected
