from datetime import datetime, timezone

from app.eda import (
    OBJECTIVE_POLICY_VERSION,
    build_belief_state,
    build_episode,
    default_objective_policy,
    make_canonical_event,
)
from app.models import DecisionRecord, MarketInput, Mode


def market_input():
    return MarketInput(
        market_id="m-eda",
        question="Will the event resolve yes?",
        market_type="binary",
        price=0.42,
        liquidity=1000,
        source="polymarket",
        snapshot_hash="snapshot-eda",
        observed_at=datetime.now(timezone.utc),
        quote_observed_at=datetime.now(timezone.utc),
        yes_ask=0.44,
        no_ask=0.62,
        model_probability=0.58,
        model_lower_bound=0.50,
        model_upper_bound=0.65,
        model_uncertainty=0.08,
        model_version="test_model_v1",
    )


def decision():
    return DecisionRecord(
        id="decision-eda",
        mode=Mode.PAPER,
        market_id="m-eda",
        strategy_id="reference_class",
        action="BUY",
        side="YES",
        size=0.01,
        price=0.42,
        fair_probability=0.58,
        confidence=0.8,
        risk_score=4,
        edge=0.12,
        executable_price=0.44,
        rationale="test",
        source="polymarket",
        model_version="test_model_v1",
        model_probability=0.58,
        model_lower_bound=0.50,
        model_upper_bound=0.65,
        model_uncertainty=0.08,
        snapshot_hash="snapshot-eda",
    )


def test_canonical_event_preserves_source_and_quality():
    event = make_canonical_event(
        "polymarket",
        "market_snapshot",
        {"market_id": "m-eda"},
        source_event_id="snapshot-eda",
    )
    assert event.schema_version == "eda_event_v1"
    assert event.source_event_id == "snapshot-eda"
    assert event.quality.valid is True


def test_belief_state_has_provenance_and_uncertainty():
    event = make_canonical_event("test", "market_input", {})
    belief = build_belief_state(market_input(), source_event_id=event.event_id)
    assert belief.market_id == "m-eda"
    assert belief.provenance["price"] == [event.event_id]
    assert belief.uncertainty_intervals["model_probability"] == [0.50, 0.65]


def test_episode_captures_versions_and_action_set():
    d = decision()
    belief = build_belief_state(market_input())
    episode = build_episode(d, market_input(), belief, default_objective_policy())
    assert episode.objective_policy_version == OBJECTIVE_POLICY_VERSION
    assert {item["action"] for item in episode.candidate_actions} == {"DO NOTHING", "WAIT", "BUY YES", "BUY NO"}
    assert episode.selected_action["side"] == "YES"
    assert episode.belief_state.belief_state_id == belief.belief_state_id
