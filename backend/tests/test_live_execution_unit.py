from app.live_execution import ControlledVenue, VenueError, deterministic_client_order_id
from app.models import DecisionRecord, Mode


def _decision():
    return DecisionRecord(id='decision-test', mode=Mode.LIVE, market_id='market-test', strategy_id='reference_class',
                          action='BUY', side='YES', size=.1, price=.4, fair_probability=.6, confidence=.8,
                          risk_score=5, edge=.1, executable_price=.45, expected_value=.01, rationale='test')


def test_client_order_id_is_deterministic():
    decision = _decision()
    assert deterministic_client_order_id(decision) == deterministic_client_order_id(decision)


def test_controlled_venue_partial_fill_and_cancel():
    venue = ControlledVenue(partial_fill=.5)
    result = venue.submit_order({'client_order_id': 'test', 'market_id': 'm', 'side': 'YES', 'size': .1, 'price': .4})
    assert result['status'] == 'partially_filled'
    assert result['filled_size'] == .05
    canceled = venue.cancel_order(result['venue_order_id'])
    assert canceled['status'] == 'canceled'


def test_controlled_venue_failure_is_explicit():
    venue = ControlledVenue(); venue.fail = True
    try:
        venue.submit_order({'client_order_id': 'test', 'market_id': 'm', 'side': 'YES', 'size': .1, 'price': .4})
    except VenueError as exc:
        assert exc.retryable is True
        assert exc.uncertain is True
    else:
        raise AssertionError('controlled outage did not fail')
