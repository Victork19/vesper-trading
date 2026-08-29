from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.live_execution import PolymarketVenue, VenueError, _status


FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LIVE", "accepted"),
        ("MATCHED", "filled"),
        ("PARTIAL", "partially_filled"),
        ("CANCELED", "canceled"),
        ("EXPIRED", "expired"),
        ("garbage", "unknown"),
    ],
)
def test_venue_status_mapping_is_explicit(raw, expected):
    assert _status(raw) == expected


def test_sdk_fixture_files_are_valid_json():
    files = sorted(FIXTURES.glob("*.json"))
    assert files, "SDK response fixtures are required"
    for fixture in files:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert "status" in payload


def test_real_sdk_requires_explicit_credentials(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(VenueError):
        PolymarketVenue()


@pytest.mark.integration
def test_real_sdk_fixture_contract_is_declared():
    """Live SDK integration requires an explicit, installed SDK contract."""
    sdk = pytest.importorskip("py_clob_client_v2")
    assert hasattr(sdk, "ClobClient")
    assert "py-clob-client-v2" in (Path(__file__).parents[1] / "requirements.txt").read_text()
