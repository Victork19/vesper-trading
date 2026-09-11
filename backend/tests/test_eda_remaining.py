from app.attribution import evaluate_attribution
import pytest
from app.models import ActionEvaluation, BookLevel, DecisionRecord, MarketInput, Mode, ModelRegistryRecord, ObjectivePolicy
from app.consequence import evaluate_actions
from app.model_registry import promotion_decision
from app.replay_engine import replay_episode
from app.experiential_memory import rank_memories
from app.eda import build_belief_state, build_episode, default_objective_policy


def make_market_input():
    return MarketInput(market_id="replay-m", question="Will it resolve yes?", price=.4, liquidity=1000, yes_ask=.42, no_ask=.62, yes_book_asks=[BookLevel(price=.42, size=10)], no_book_asks=[BookLevel(price=.62, size=10)], snapshot_hash="replay-snapshot")


def test_promotion_is_fail_closed_without_all_gates():
    record = ModelRegistryRecord(model_id="m", version="v1", metrics={"sample_count": 100})
    result = promotion_decision(record, operator="operator")
    assert result["approved"] is False
    assert "out_of_sample_gate" in result["blockers"]


def test_promotion_requires_independent_numeric_evidence_and_rollback():
    record = ModelRegistryRecord(model_id="m", version="v2", rollback_target="v1", metrics={
        "sample_count": 100, "out_of_sample_passed": True, "risk_gate_passed": True,
        "execution_gate_passed": True, "oos_expectancy_ci_low": .01,
        "max_drawdown": .10, "execution_reconciliation_rate": 1.0,
        "operational_error_rate": 0,
    })
    result = promotion_decision(record, operator="operator")
    assert result["approved"] is True


def test_replay_excludes_events_after_decision_boundary():
    market = make_market_input()
    decision = DecisionRecord(id="replay-d", mode=Mode.PAPER, market_id=market.market_id, strategy_id="reference_class", action="DO NOTHING", price=.4, fair_probability=.5, confidence=.5, risk_score=1, edge=0, rationale="test")
    episode = build_episode(decision, market, build_belief_state(market), default_objective_policy())
    before = {"event_id": "before", "event_type": "market_input_observed", "event_time": episode.timestamp_decision, "payload": market.model_dump(mode="json")}
    after = {"event_id": "after", "event_time": "2999-01-01T00:00:00Z", "payload": {}}
    result = replay_episode(episode, [before, after])
    assert result["eligible_event_ids"] == ["before"]
    assert result["lookahead_excluded"] == 1


def test_replay_requires_historical_observation_event():
    market = make_market_input()
    decision = DecisionRecord(id="replay-missing", mode=Mode.PAPER, market_id=market.market_id, strategy_id="reference_class", action="DO NOTHING", price=.4, fair_probability=.5, confidence=.5, risk_score=1, edge=0, rationale="test")
    episode = build_episode(decision, market, build_belief_state(market), default_objective_policy())
    try:
        replay_episode(episode, [])
    except ValueError as error:
        assert "market_input_observed" in str(error)
    else:
        raise AssertionError("replay must fail closed when historical observations are missing")


def test_replay_modes_fail_closed_without_candidate_policy():
    market = make_market_input()
    decision = DecisionRecord(id="replay-mode", mode=Mode.PAPER, market_id=market.market_id, strategy_id="reference_class", action="DO NOTHING", price=.4, fair_probability=.5, confidence=.5, risk_score=1, edge=0, rationale="test")
    episode = build_episode(decision, market, build_belief_state(market), default_objective_policy())
    try:
        replay_episode(episode, [], mode="candidate_policy")
    except ValueError as error:
        assert "candidate objective policy" in str(error)
    else:
        raise AssertionError("candidate replay must require an explicit candidate policy")


def test_objective_policy_weights_change_consequence_utility():
    market = make_market_input()
    baseline = evaluate_actions(market, fair_probability=.6, confidence=.8, uncertainty=.1)[2].expected_utility
    penalized = evaluate_actions(market, fair_probability=.6, confidence=.8, uncertainty=.1, objective_policy=ObjectivePolicy(utility_weights={"expected_return": 1, "downside_risk": 10, "transaction_cost": 1, "drawdown_risk": 1, "uncertainty": 1, "operational_risk": 1, "information_value": 0}))[2].expected_utility
    assert penalized < baseline


def test_memory_retrieval_rejects_unlinked_generated_content():
    memories = [
        {"lesson": "liquidity failure", "evidence": {"episode_id": "episode-1"}},
        {"lesson": "liquidity failure", "explanation": "generated"},
    ]
    result = rank_memories(memories, "liquidity failure")
    assert len(result) == 1
    assert result[0]["evidence"]["episode_id"] == "episode-1"


def test_replay_is_deterministic_for_identical_history():
    market = make_market_input()
    decision = DecisionRecord(id="replay-deterministic", mode=Mode.PAPER, market_id=market.market_id, strategy_id="reference_class", action="DO NOTHING", price=.4, fair_probability=.5, confidence=.5, risk_score=1, edge=0, rationale="test")
    episode = build_episode(decision, market, build_belief_state(market), default_objective_policy())
    event = {"event_id": "observation", "event_type": "market_input_observed", "event_time": episode.timestamp_decision, "payload": market.model_dump(mode="json")}
    assert replay_episode(episode, [event]) == replay_episode(episode, [event])


def test_attribution_uses_per_unit_realized_pnl():
    decision = DecisionRecord(id="attr-d", mode=Mode.PAPER, market_id="m", strategy_id="s", action="BUY", side="YES", size=.2, price=.4, fair_probability=.6, confidence=.8, risk_score=1, edge=.2, rationale="test", outcome="win", pnl=.04, resolved_yes=True, paper_fill_fraction=1, paper_execution_price=.4)
    report = evaluate_attribution(decision, action_evaluations=[ActionEvaluation(action="BUY YES", side="YES", expected_utility=.2)])
    assert report["metrics"]["realized_unit_pnl"] == pytest.approx(.2)