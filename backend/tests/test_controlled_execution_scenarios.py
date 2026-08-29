import pytest
from app.live_execution import ControlledVenue, VenueError

@pytest.mark.controlled_account
def test_partial_fill_is_visible_and_cancel_is_terminal():
    venue=ControlledVenue(partial_fill=.4)
    accepted=venue.submit_order({'client_order_id':'scenario-1','market_id':'m','side':'YES','size':1.0,'price':.4})
    assert accepted['status']=='partially_filled'
    assert accepted['filled_size']==pytest.approx(.4)
    venue.cancel_order(accepted['venue_order_id'])
    assert venue.get_order(accepted['venue_order_id'])['status']=='canceled'

@pytest.mark.fault_injection
def test_uncertain_outage_is_not_safe_to_retry():
    venue=ControlledVenue();venue.fail=True
    with pytest.raises(VenueError) as error:
        venue.submit_order({'client_order_id':'scenario-2','market_id':'m','side':'YES','size':1.0,'price':.4})
    assert error.value.uncertain is True
