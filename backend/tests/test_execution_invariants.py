from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from app.live_execution import ControlledVenue, ExecutionCircuit, VenueError


def test_controlled_venue_partial_fill_is_bounded():
    venue = ControlledVenue(partial_fill=0.5)
    response = venue.submit_order(
        {"client_order_id": "fixture-1", "market_id": "m", "side": "BUY", "size": 2, "price": 0.4}
    )
    assert 0 < response["filled_size"] <= 2
    assert response["status"] == "partially_filled"


def test_controlled_outage_marks_submission_uncertain():
    venue = ControlledVenue()
    venue.fail = True
    with pytest.raises(VenueError) as error:
        venue.submit_order({"client_order_id": "fixture-2", "size": 1, "price": 0.5})
    assert error.value.retryable and error.value.uncertain


@pytest.mark.fault_injection
def test_cancellation_response_must_be_reconciled_by_follow_up_query():
    venue = ControlledVenue(partial_fill=0.25)
    submitted = venue.submit_order(
        {"client_order_id": "fixture-3", "market_id": "m", "side": "BUY", "size": 4, "price": 0.25}
    )
    canceled = venue.cancel_order(submitted["venue_order_id"])
    final = venue.get_order(submitted["venue_order_id"])
    assert canceled["status"] == "canceled"
    assert final["status"] == "canceled"
    assert final["filled_size"] == submitted["filled_size"]


@pytest.mark.fault_injection
def test_circuit_first_initialization_is_single_row(pg_connection_factory):
    """Concurrent DB circuit initialization must not create duplicate rows."""
    # This is deliberately opt-in and uses a unique test circuit name.
    name = "test_first_initialization"
    errors: list[Exception] = []

    def worker():
        try:
            with pg_connection_factory() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (name,))
                connection.execute(
                    "INSERT INTO execution_circuit(name,state,failures,updated_at) "
                    "VALUES(%s,'closed',0,NOW()) ON CONFLICT(name) DO NOTHING",
                    (name,),
                )
        except Exception as exc:  # pragma: no cover - reported by assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    with pg_connection_factory() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM execution_circuit WHERE name=%s", (name,)
        ).fetchone()[0]
        connection.execute("DELETE FROM execution_circuit WHERE name=%s", (name,))
    assert count == 1


@pytest.mark.integration
def test_append_only_execution_tables_reject_mutation(pg_connection):
    """The database, not only Python, must protect execution history."""
    pytest.importorskip("psycopg")
    # Use savepoints so this test leaves no rows behind.
    with pg_connection.transaction():
        pg_connection.execute(
            "INSERT INTO execution_ledger(event_id,idempotency_key,event_type) "
            "VALUES('test-ledger-event','test-ledger-key','test')"
        )
        with pytest.raises(Exception):
            pg_connection.execute(
                "UPDATE execution_ledger SET event_type='tampered' "
                "WHERE event_id='test-ledger-event'"
            )
