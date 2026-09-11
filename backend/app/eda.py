"""Foundational Experiential Decision Architecture contracts.

This module deliberately contains schemas and deterministic builders only. It
does not select trades or weaken the existing risk authority boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .models import (
    ActionEvaluation,
    AttentionPlan,
    BeliefState,
    CanonicalEvent,
    DecisionEpisode,
    DecisionRecord,
    EventQuality,
    HotState,
    MarketInput,
    ObjectivePolicy,
    PolicyProposal,
    now_iso,
)

OBJECTIVE_POLICY_VERSION = "objective_policy_v1"
POLICY_VERSION = "deterministic_policy_v2"
RISK_POLICY_VERSION = "risk_policy_v1"
DATA_VERSION = "market_input_v1"


def default_objective_policy() -> ObjectivePolicy:
    return ObjectivePolicy(version=OBJECTIVE_POLICY_VERSION)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _event_time(value: Any = None) -> str:
    if value is None:
        return now_iso()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def make_canonical_event(
    source: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    event_time: Any = None,
    episode_id: str | None = None,
    source_event_id: str | None = None,
    quality: EventQuality | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id="eda_event_" + uuid.uuid4().hex,
        episode_id=episode_id,
        source=source,
        source_event_id=source_event_id,
        event_type=event_type,
        event_time=_event_time(event_time),
        payload=payload,
        quality=quality or EventQuality(),
    )


def insert_canonical_event_json(connection, database, event: CanonicalEvent) -> None:
    """Database adapter variant used by callers that expose Jsonb helpers."""
    connection.execute(
        """INSERT INTO eda_events
        (event_id,episode_id,source,source_event_id,event_type,event_time,
         ingestion_time,payload,schema_version,quality)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(event_id) DO NOTHING""",
        (
            event.event_id,
            event.episode_id,
            event.source,
            event.source_event_id,
            event.event_type,
            event.event_time,
            event.ingestion_time,
            database.json(event.payload),
            event.schema_version,
            database.json(event.quality.model_dump(mode="json")),
        ),
    )


def build_belief_state(market: MarketInput, *, source_event_id: str | None = None, hot_state: HotState | None = None) -> BeliefState:
    snapshot_id = market.snapshot_hash or "manual_input"
    belief_id = "belief_" + canonical_hash(
        {"market_id": market.market_id, "snapshot": snapshot_id, "observed_at": market.observed_at}
    )[:20]
    stale_fields: list[str] = []
    if market.observed_at is None:
        stale_fields.append("observed_at")
    if market.quote_observed_at is None and market.source.startswith("polymarket"):
        stale_fields.append("quote_observed_at")
    provenance = {
        "market_observation": [source_event_id or snapshot_id],
        "price": [source_event_id or snapshot_id],
        "liquidity": [source_event_id or snapshot_id],
        "regime": [source_event_id or snapshot_id],
    }
    observable = {
        "question": market.question,
        "price": market.price,
        "liquidity": market.liquidity,
        "volume_24h": market.volume_24h,
        "volume_known": market.volume_known,
        "resolution_hours": market.resolution_hours,
        "quality_score": market.quality_score,
        "market_status": market.market_status,
        "quote_skew_seconds": market.quote_skew_seconds,
        "yes_bid": market.yes_bid,
        "yes_ask": market.yes_ask,
        "no_bid": market.no_bid,
        "no_ask": market.no_ask,
        "portfolio_heat": hot_state.portfolio_heat if hot_state else None,
        "open_risk": hot_state.open_risk if hot_state else None,
        "correlation_regime": hot_state.correlation_regime if hot_state else None,
        "capacity_utilization": hot_state.capacity_utilization if hot_state else None,
        "strategy_trust": hot_state.trust if hot_state else {},
    }
    distributions = {
        "market_price": market.price,
        "reference_rate": market.reference_rate,
        "model_probability": market.model_probability,
        "signals": market.signals,
    }
    intervals = {}
    if market.model_lower_bound is not None or market.model_upper_bound is not None:
        intervals["model_probability"] = [market.model_lower_bound, market.model_upper_bound]
    return BeliefState(
        belief_state_id=belief_id,
        market_id=market.market_id,
        observable_features=observable,
        probability_distributions=distributions,
        uncertainty_intervals=intervals,
        regime_beliefs={market.regime: 1.0},
        open_questions=["outcome_resolution"] if market.market_status == "active" else [],
        stale_fields=stale_fields,
        provenance=provenance,
        source_event_ids=[source_event_id] if source_event_id else [snapshot_id],
    )


def build_episode(
    decision: DecisionRecord,
    market: MarketInput,
    belief_state: BeliefState,
    objective_policy: ObjectivePolicy,
    *,
    attention_plan: AttentionPlan | None = None,
    action_evaluations: list[ActionEvaluation] | None = None,
    policy_proposal: PolicyProposal | None = None,
) -> DecisionEpisode:
    yes_available = market.yes_ask is not None or bool(market.yes_book_asks)
    no_available = market.no_ask is not None or bool(market.no_book_asks)
    candidates = [
        {"action": "DO NOTHING", "status": "available", "reason": "risk-free baseline"},
        {"action": "WAIT", "status": "available", "reason": "defer while preserving optionality"},
        {"action": "BUY YES", "status": "available" if yes_available else "not_available"},
        {"action": "BUY NO", "status": "available" if no_available else "not_available"},
    ]
    timestamp = decision.created_at
    return DecisionEpisode(
        episode_id=decision.episode_id or "episode_" + uuid.uuid4().hex,
        decision_id=decision.id,
        timestamp_start=timestamp,
        timestamp_decision=timestamp,
        observation_snapshot=market.model_dump(mode="json"),
        available_information={
            "source": market.source,
            "snapshot_hash": market.snapshot_hash,
            "objective_policy": objective_policy.model_dump(mode="json"),
            "observed_at": market.observed_at.isoformat() if market.observed_at else None,
            "quote_observed_at": market.quote_observed_at.isoformat() if market.quote_observed_at else None,
            "model_provenance": market.model_provenance,
        },
        belief_state=belief_state,
        forecasts={
            "fair_probability": decision.fair_probability,
            "raw_model_probability": decision.raw_model_probability,
            "model_probability": decision.model_probability,
            "edge": decision.edge,
        },
        uncertainty={
            "confidence": decision.confidence,
            "model_uncertainty": decision.model_uncertainty,
            "lower_bound": decision.model_lower_bound,
            "upper_bound": decision.model_upper_bound,
        },
        regime={"label": decision.regime, "source": "market_input"},
        candidate_actions=candidates,
        consequence_predictions={item.action: {"expected_utility": item.expected_utility, "expected_value_per_unit": item.expected_value_per_unit, "available": item.available, "model_dependent": item.model_dependent} for item in (action_evaluations or [])},
        counterfactuals={item.action: {"expected_utility": item.expected_utility, "available": item.available, "observed": item.action == f"BUY {decision.side}" and decision.action == "BUY"} for item in (action_evaluations or [])},
        selected_action={
            "action": decision.action,
            "side": decision.side,
            "size": decision.size,
            "executable_price": decision.executable_price,
        },
        risk_decision={
            "authorized": decision.size > 0,
            "risk_score": decision.risk_score,
            "gates": decision.gates,
            "authority": objective_policy.authority_boundary,
        },
        model_versions={
            "forecast": decision.model_version,
            "fill": decision.fill_model_version,
        },
        attention_plan=attention_plan.model_dump(mode="json") if attention_plan else {},
        information_requests=[item.model_dump(mode="json") for item in attention_plan.information_requests] if attention_plan else [],
        action_evaluations=[item.model_dump(mode="json") for item in (action_evaluations or [])],
        policy_proposal=policy_proposal.model_dump(mode="json") if policy_proposal else {},
        policy_version=decision.policy_version,
        risk_policy_version=decision.risk_policy_version,
        data_versions={"market_input": DATA_VERSION, "snapshot": market.snapshot_hash or "none"},
        objective_policy_version=objective_policy.version,
    )


def episode_hash(episode: DecisionEpisode) -> str:
    return canonical_hash(episode.model_dump(mode="json"))


def validate_episode_integrity(episode: DecisionEpisode) -> None:
    if episode.schema_version != "eda_episode_v1":
        raise ValueError("unsupported EDA episode schema")
    if not episode.episode_id or not episode.decision_id:
        raise ValueError("EDA episode identity is required")
    if episode.belief_state.market_id != episode.observation_snapshot.get("market_id"):
        raise ValueError("belief state market does not match episode observation")
    if not episode.belief_state.source_event_ids:
        raise ValueError("EDA episode requires belief provenance")
    if not episode.candidate_actions:
        raise ValueError("EDA episode requires candidate actions")
