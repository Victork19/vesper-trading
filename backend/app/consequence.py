"""Deterministic consequence and counterfactual evaluation."""

from __future__ import annotations

from .models import ActionEvaluation, HotState, MarketInput, ObjectivePolicy

CONSEQUENCE_POLICY_VERSION = "consequence_policy_v1"


def _levels(market: MarketInput, side: str):
    return market.yes_book_asks if side == "YES" else market.no_book_asks


def _ask(market: MarketInput, side: str) -> float | None:
    quote = market.yes_ask if side == "YES" else market.no_ask
    levels = _levels(market, side)
    if quote is not None:
        return float(quote)
    return float(levels[0].price) if levels else None


def _evaluation(
    market: MarketInput,
    *,
    side: str,
    probability: float,
    uncertainty: float,
    confidence: float,
    hot_state: HotState | None,
    objective_policy: ObjectivePolicy,
) -> ActionEvaluation:
    reasons: list[str] = []
    ask = _ask(market, side)
    levels = _levels(market, side)
    depth = sum(float(level.size) for level in levels if level.size > 0)
    if ask is None:
        reasons.append("executable_ask_missing")
    if not levels:
        reasons.append("ask_depth_missing")
    if market.market_status != "active":
        reasons.append("market_not_active")
    available = not reasons
    slippage = max(0.0, float(market.slippage_bps)) / 10000.0
    transaction_cost = (ask * slippage + max(0.0, float(market.fee_rate)) * (1.0 - ask)) if ask is not None else 0.0
    executable = min(1.0, ask + transaction_cost) if ask is not None else None
    expected_value = probability - executable if executable is not None else 0.0
    # Candidate evaluation is size-independent; the execution adapter later
    # evaluates exact depth for the approved quantity. Any positive displayed
    # depth therefore establishes availability without pretending it supports
    # an unlimited order.
    fill_probability = 1.0 if available and depth > 0 else 0.0
    downside = (1.0 - probability) * (executable or 0.0)
    portfolio_penalty = .0
    if hot_state is not None and hot_state.correlation_regime in {"elevated", "crisis"}:
        portfolio_penalty = .01
        reasons.append("correlation_regime_penalty")
    weights = objective_policy.utility_weights
    uncertainty_penalty = max(0.0, min(1.0, uncertainty)) * weights.get('uncertainty', 1.0)
    utility = (
        weights.get('expected_return', 1.0) * expected_value * fill_probability * max(.05, min(1.0, confidence))
        - weights.get('downside_risk', 1.0) * downside
        - weights.get('transaction_cost', 1.0) * transaction_cost
        - uncertainty_penalty * .02
        - weights.get('drawdown_risk', 1.0) * portfolio_penalty
    )
    if not available:
        utility = -1.0
    return ActionEvaluation(
        action=f"BUY {side}",
        side=side,
        available=available,
        executable_price=executable,
        probability=probability,
        expected_value_per_unit=expected_value,
        expected_utility=utility,
        downside_risk=downside,
        transaction_cost=transaction_cost,
        fill_probability=fill_probability,
        liquidity_depth=depth,
        time_to_resolution_hours=market.resolution_hours,
        probability_of_loss=1.0 - probability,
        tail_risk=downside,
        liquidity_impact=1.0 / max(1.0, depth),
        portfolio_effect=-portfolio_penalty,
        opportunity_cost=max(0.0, probability - (1.0 - probability)),
        reversibility=0.5,
        paper_fill_price=executable,
        reasons=reasons,
        model_dependent=True,
        policy_version=CONSEQUENCE_POLICY_VERSION,
    )


def evaluate_actions(
    market: MarketInput,
    *,
    fair_probability: float,
    confidence: float,
    uncertainty: float,
    hot_state: HotState | None = None,
    objective_policy: ObjectivePolicy | None = None,
) -> list[ActionEvaluation]:
    """Evaluate both executable contract sides and the no-action baseline."""
    objective_policy = objective_policy or ObjectivePolicy()
    wait_utility = max(0.0, min(1.0, uncertainty)) * objective_policy.utility_weights.get('information_value', 0.0)
    return [
        ActionEvaluation(
            action="DO NOTHING",
            available=True,
            probability=None,
            expected_value_per_unit=0.0,
            expected_utility=0.0,
            downside_risk=0.0,
            transaction_cost=0.0,
            fill_probability=1.0,
            liquidity_depth=0.0,
            time_to_resolution_hours=market.resolution_hours,
            reasons=["risk_free_baseline"],
            model_dependent=False,
            policy_version=CONSEQUENCE_POLICY_VERSION,
        ),
        ActionEvaluation(
            action="WAIT",
            available=market.market_status == "active",
            expected_utility=wait_utility,
            fill_probability=1.0,
            time_to_resolution_hours=market.resolution_hours,
            reversibility=1.0,
            reasons=["preserve_optionality"],
            model_dependent=True,
            policy_version=CONSEQUENCE_POLICY_VERSION,
        ),
        _evaluation(market, side="YES", probability=max(0.0, min(1.0, fair_probability)), uncertainty=uncertainty, confidence=confidence, hot_state=hot_state, objective_policy=objective_policy),
        _evaluation(market, side="NO", probability=max(0.0, min(1.0, 1.0 - fair_probability)), uncertainty=uncertainty, confidence=confidence, hot_state=hot_state, objective_policy=objective_policy),
    ]
