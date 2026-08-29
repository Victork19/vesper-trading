import sys
import types

import pytest

from app.live_execution import PolymarketVenue, VenueError


class FakeOrderArgs:
    def __init__(self, *, price, size, side, token_id, client_order_id):
        self.price = price
        self.size = size
        self.side = side
        self.token_id = token_id
        self.client_order_id = client_order_id


class FakeOptions:
    def __init__(self, *, tick_size, neg_risk):
        self.tick_size = tick_size
        self.neg_risk = neg_risk


class FakeApiCreds:
    def __init__(self, *, api_key, api_secret, api_passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase


class FakeClient:
    last = None

    def __init__(self, host, *, key, chain_id, signature_type, funder):
        self.host = host
        self.key = key
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder = funder
        self.creds = None
        self.created = None
        self.posted = None
        FakeClient.last = self

    def set_api_creds(self, creds):
        self.creds = creds

    def create_order(self, args, options):
        self.created = (args, options)
        return {'signed': True, 'args': args}

    def post_order(self, signed, order_type):
        self.posted = (signed, order_type)
        return {'orderID': 'venue-1', 'status': 'LIVE'}

    def get_order(self, order_id):
        return {'id': order_id, 'status': 'LIVE'}

    def get_orders(self):
        return {'orders': []}

    def cancel(self, order_id):
        return {'id': order_id, 'status': 'CANCELED'}

    def get_ok(self):
        return {'ok': True}

    def get_balance_allowance(self):
        return {'collateral_balance': 100, 'allowances': {}}


@pytest.fixture
def sdk_modules(monkeypatch):
    root = types.ModuleType('py_clob_client_v2')
    root.ClobClient = FakeClient
    types_mod = types.ModuleType('py_clob_client_v2.clob_types')
    types_mod.ApiCreds = FakeApiCreds
    types_mod.OrderArgs = FakeOrderArgs
    types_mod.OrderType = types.SimpleNamespace(GTC='GTC')
    types_mod.Side = types.SimpleNamespace(BUY='BUY', SELL='SELL')
    types_mod.PartialCreateOrderOptions = FakeOptions
    monkeypatch.setitem(sys.modules, 'py_clob_client_v2', root)
    monkeypatch.setitem(sys.modules, 'py_clob_client_v2.clob_types', types_mod)
    for key, value in {
        'POLYMARKET_PRIVATE_KEY': 'private',
        'POLYMARKET_API_KEY': 'api',
        'POLYMARKET_API_SECRET': 'secret',
        'POLYMARKET_API_PASSPHRASE': 'pass',
        'POLYMARKET_FUNDER_ADDRESS': '0xfunder',
        'POLYMARKET_SIGNATURE_TYPE': '2',
    }.items():
        monkeypatch.setenv(key, value)


def test_typed_sdk_workflow_maps_buy_and_credentials(sdk_modules):
    venue = PolymarketVenue()
    result = venue.submit_order({
        'client_order_id': 'vesper-1', 'token_id': 'yes-token', 'side': 'BUY',
        'price': .41, 'size': .2, 'tick_size': '0.01', 'neg_risk': True,
    })
    client = FakeClient.last
    args, options = client.created
    assert client.creds.api_key == 'api'
    assert client.signature_type == 2
    assert client.funder == '0xfunder'
    assert args.side == 'BUY'
    assert args.token_id == 'yes-token'
    assert args.client_order_id == 'vesper-1'
    assert options.tick_size == '0.01'
    assert options.neg_risk is True
    assert client.posted[1] == 'GTC'
    assert result['venue_order_id'] == 'venue-1'


def test_typed_adapter_fails_closed_without_l2_credentials(sdk_modules, monkeypatch):
    monkeypatch.delenv('POLYMARKET_API_SECRET')
    with pytest.raises(VenueError, match='L2 API credentials'):
        PolymarketVenue()


def test_typed_adapter_rejects_outcome_as_venue_side(sdk_modules):
    venue = PolymarketVenue()
    with pytest.raises(VenueError, match='token identity and venue side BUY'):
        venue.submit_order({'client_order_id': 'x', 'token_id': 'yes-token', 'side': 'YES',
                            'price': .4, 'size': .1, 'tick_size': '0.01', 'neg_risk': False})
