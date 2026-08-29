import time

import pytest

from app.reconciliation import _TERMINAL, _venue_id
from app.worker import _deadline_call


def test_venue_id_accepts_supported_venue_payload_shapes():
    assert _venue_id({'orderID': 'abc'}) == 'abc'
    assert _venue_id({'venue_order_id': 'xyz'}) == 'xyz'
    assert _venue_id({}) == ''


def test_reconciliation_terminal_states_are_explicit():
    assert _TERMINAL == {'filled', 'canceled', 'expired', 'rejected', 'failed'}
    assert 'unknown' not in _TERMINAL


def test_venue_operation_deadline_fails_fast():
    def blocked():
        time.sleep(0.2)

    with pytest.raises(TimeoutError):
        _deadline_call(blocked, timeout=0.02)
