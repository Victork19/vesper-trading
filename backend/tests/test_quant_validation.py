from types import SimpleNamespace

import pytest

from app.engines import collateral_cost, kelly_contracts
from app.research_validation import (
    canonical_dependency_metadata,
    executable_benchmark,
    is_research_eligible,
    population_drift,
    research_exposure,
    risk_metrics,
    walk_forward_splits,
)


def observation(**overrides):
    base = dict(
        research_eligible=True,
        source="polymarket-clob",
        snapshot_hash="snap-1",
        quote_observed_at="2026-08-20T00:00:00Z",
        model_version="model-1",
        outcome="win",
        resolved_yes=True,
        execution_reconciled=True,
        size=2.0,
        paper_fill_fraction=1.0,
        paper_execution_price=.25,
        price=.25,
        fee_rate=.01,
        slippage_bps=10,
        created_at="2026-08-19T00:00:00Z",
        resolved_at="2026-08-20T00:00:00Z",
        market_context={
            "event_family": "family-a",
            "dependency_id": "dependency-a",
            "resolution_source": "venue",
        },
        mode="paper",
        side="YES",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_collateral_heat_is_price_sensitive():
    cheap = collateral_cost(.01)
    expensive = collateral_cost(.99)
    assert expensive / cheap > 90


def test_kelly_returns_contracts_not_a_bankroll_fraction():
    contracts = kelly_contracts(.60, .50, bankroll=100.0, fraction=.5)
    # Half-Kelly allocates $10; at a $0.50 collateral cost that is 20
    # contracts, rather than returning the bankroll allocation as a count.
    assert contracts == pytest.approx(20.0)
    assert contracts > 1.0


def test_executable_benchmark_includes_fee_and_slippage():
    benchmark = executable_benchmark(observation())
    assert benchmark["cost"] > .25
    assert benchmark["source"] == "yes_ask" or benchmark["source"] == "fallback"


def test_research_exposure_is_collateral_not_contract_count():
    assert research_exposure(observation()) == pytest.approx(2 * (.25 + .25 * 10 / 10000 + .01 * (1 - .25)))


def test_research_eligibility_rejects_manual_or_unreconciled_observations():
    assert is_research_eligible(observation())
    assert not is_research_eligible(observation(market_context={"event_family": "family-a", "dependency_id": "dependency-a", "resolution_source": "manual"}))
    assert not is_research_eligible(observation(execution_reconciled=False))


def test_dependency_metadata_retains_numeric_resolution_identity():
    first = canonical_dependency_metadata("Will BTC be above 60000?", "crypto", resolution_start="2026-08-20T00:00:00Z", resolution_end="2026-08-20T01:00:00Z")
    second = canonical_dependency_metadata("Will BTC be above 61000?", "crypto", resolution_start="2026-08-20T00:00:00Z", resolution_end="2026-08-20T01:00:00Z")
    assert first["event_family"] != second["event_family"]
    assert first["horizon_minutes"] == 60


def test_walk_forward_splits_embargo_and_dependency_overlap():
    items = []
    for index in range(8):
        items.append(SimpleNamespace(created_at=f"2026-08-{index + 1:02d}T00:00:00Z", market_context={"dependency_id": f"d-{index // 2}"}, market_id=str(index)))
    splits = walk_forward_splits(items, train_size=3, test_size=2, embargo=1)
    assert splits
    for train, test in splits:
        assert not ({x.market_context["dependency_id"] for x in train} & {x.market_context["dependency_id"] for x in test})


def test_population_drift_gate_detects_distribution_shift():
    result = population_drift([.1] * 100, [.9] * 100)
    assert result["drift"] is True
    assert result["psi"] > .2


def test_risk_metrics_report_return_and_drawdown_not_only_win_rate():
    result = risk_metrics([.01, .01, -.05], [.10, .10, .10])
    assert result["win_rate"] == pytest.approx(2 / 3)
    assert result["return_on_capital"] < 0
    assert result["max_drawdown"] == pytest.approx(.05)
