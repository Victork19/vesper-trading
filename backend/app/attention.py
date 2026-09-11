"""Deterministic attention and information-acquisition planning."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .models import AttentionPlan, HotState, InformationRequest, MarketInput

ATTENTION_POLICY_VERSION = "attention_policy_v1"
INFORMATION_POLICY_VERSION = "information_policy_v1"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _age_seconds(value) -> float | None:
    if value is None:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return None


def plan_attention(
    market: MarketInput,
    *,
    strategy_id: str = "reference_class",
    hot_state: HotState | None = None,
    max_portfolio_heat: float = 0.20,
) -> AttentionPlan:
    """Rank analysis priority and describe evidence the system should acquire.

    This is intentionally deterministic. It does not authorize a trade; the
    risk and policy layers remain the authority for action selection.
    """
    uncertainty = _clamp(market.model_uncertainty if market.model_uncertainty is not None else .5)
    model_gap = abs((market.model_probability if market.model_probability is not None else market.price) - market.price)
    expected_value = _clamp(min(1.0, market.liquidity / 10000.0) * (.35 + min(.65, model_gap * 4.0)))
    time_sensitivity = _clamp(1.0 / (1.0 + max(0.0, market.resolution_hours) / 24.0))
    actionability = _clamp(
        (.5 if market.market_status == "active" else 0.0)
        + (.25 if market.yes_ask is not None or market.yes_book_asks else 0.0)
        + (.25 if market.no_ask is not None or market.no_book_asks else 0.0)
    )
    analysis_cost = _clamp(.25 + (.25 if market.source == "manual" else 0.0) + (.25 if market.resolution_hours > 168 else 0.0))
    attention_score = _clamp(expected_value * max(.1, uncertainty) * max(.1, time_sensitivity) * actionability / max(.25, analysis_cost))
    requests: list[InformationRequest] = []
    reasons: list[str] = []

    if market.source.startswith("polymarket") and not market.snapshot_hash:
        requests.append(InformationRequest(request_id="info_snapshot_" + hashlib.sha256(market.market_id.encode()).hexdigest()[:12], request_type="refresh_market_snapshot", target=market.market_id, reason="A verified point-in-time snapshot is required for research provenance.", expected_value=expected_value * .5, cost=.1, required=True, policy_version=INFORMATION_POLICY_VERSION))
        reasons.append("verified_snapshot_missing")
    if market.source.startswith("polymarket") and (not market.yes_book_asks or not market.no_book_asks):
        requests.append(InformationRequest(request_id="info_books_" + hashlib.sha256((market.market_id + ":books").encode()).hexdigest()[:12], request_type="refresh_order_books", target=market.market_id, reason="Both executable contract books are needed to evaluate fill quality.", expected_value=expected_value * .8, cost=.15, required=True, policy_version=INFORMATION_POLICY_VERSION))
        reasons.append("executable_books_missing")
    quote_age = _age_seconds(market.quote_observed_at)
    if quote_age is not None and quote_age > 15:
        requests.append(InformationRequest(request_id="info_quote_" + hashlib.sha256((market.market_id + ":quote").encode()).hexdigest()[:12], request_type="refresh_quotes", target=market.market_id, reason="Executable quotes are stale for attention planning.", expected_value=expected_value * .7, cost=.1, required=True, policy_version=INFORMATION_POLICY_VERSION))
        reasons.append("quotes_stale")
    if market.resolution_hours <= 1:
        requests.append(InformationRequest(request_id="info_resolution_" + hashlib.sha256((market.market_id + ":resolution").encode()).hexdigest()[:12], request_type="check_resolution_status", target=market.market_id, reason="Short-horizon markets have elevated timing risk.", expected_value=expected_value * .4, cost=.05, required=False, policy_version=INFORMATION_POLICY_VERSION))
        reasons.append("time_sensitive_market")
    if uncertainty >= .25 and model_gap >= .03:
        requests.append(InformationRequest(request_id="info_confirmation_" + hashlib.sha256((market.market_id + ":confirmation").encode()).hexdigest()[:12], request_type="seek_confirming_evidence", target=market.market_id, reason="Material uncertainty and model/market disagreement justify confirmation.", expected_value=expected_value * uncertainty, cost=.2, required=False, policy_version=INFORMATION_POLICY_VERSION))
        reasons.append("material_uncertainty")

    if market.market_status != "active":
        disposition = "skip"
        reasons.append("market_not_active")
    elif any(item.required for item in requests):
        disposition = "request_information"
    elif attention_score < .15:
        disposition = "defer"
        reasons.append("low_attention_score")
    else:
        disposition = "analyze"

    if hot_state is not None and hot_state.portfolio_heat >= max_portfolio_heat:
        reasons.append("portfolio_near_heat_limit")
        actionability *= .5
        attention_score = _clamp(attention_score * .5)

    return AttentionPlan(
        plan_id="attention_" + hashlib.sha256(f"{market.market_id}:{market.snapshot_hash or 'none'}".encode()).hexdigest()[:20],
        market_id=market.market_id,
        strategy_id=strategy_id,
        attention_score=attention_score,
        expected_decision_value=expected_value,
        uncertainty=uncertainty,
        time_sensitivity=time_sensitivity,
        actionability=_clamp(actionability),
        analysis_cost=analysis_cost,
        disposition=disposition,
        reasons=reasons,
        information_requests=requests,
        policy_version=ATTENTION_POLICY_VERSION,
    )

