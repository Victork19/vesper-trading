from app.attention import plan_attention
from app.consequence import evaluate_actions
from app.models import BookLevel, MarketInput
from app.policy import propose_action


def market(**overrides):
    values = dict(
        market_id="phase-456",
        question="Will the event resolve yes?",
        market_type="binary",
        price=0.40,
        liquidity=25000,
        volume_24h=100000,
        source="polymarket-clob",
        snapshot_hash="snapshot-phase-456",
        yes_ask=0.42,
        no_ask=0.62,
        yes_book_asks=[BookLevel(price=0.42, size=10)],
        no_book_asks=[BookLevel(price=0.62, size=10)],
        reference_rate=0.60,
    )
    values.update(overrides)
    return MarketInput(**values)


def test_attention_requests_missing_executable_evidence():
    plan = plan_attention(market(yes_book_asks=[], no_book_asks=[]))
    assert plan.disposition == "request_information"
    assert any(item.request_type == "refresh_order_books" for item in plan.information_requests)


def test_consequence_evaluation_contains_no_action_and_both_contracts():
    evaluations = evaluate_actions(market(), fair_probability=0.60, confidence=.8, uncertainty=.1)
    assert {item.action for item in evaluations} == {"DO NOTHING", "WAIT", "BUY YES", "BUY NO"}
    yes = next(item for item in evaluations if item.action == "BUY YES")
    assert yes.available is True
    assert yes.expected_value_per_unit > 0


def test_policy_rejects_unavailable_preferred_side():
    evaluations = evaluate_actions(market(yes_book_asks=[]), fair_probability=0.60, confidence=.8, uncertainty=.1)
    proposal = propose_action(evaluations, "YES")
    assert proposal.status == "rejected"
    assert proposal.proposed_action == "DO NOTHING"

