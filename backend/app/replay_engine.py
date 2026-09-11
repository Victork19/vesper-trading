"""Point-in-time EDA replay with an explicit no-look-ahead boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from .consequence import evaluate_actions
from .eda import build_belief_state
from .models import MarketInput, ObjectivePolicy
from .policy import propose_action


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def replay_episode(episode, events, *, mode: str = "historical", as_of: str | None = None, objective_policy: ObjectivePolicy | None = None) -> dict:
    if mode not in {"historical", "current_policy", "candidate_policy", "stress"}:
        raise ValueError("unsupported replay mode")
    if mode == "candidate_policy" and objective_policy is None:
        raise ValueError("candidate_policy replay requires a candidate objective policy")
    boundary = _timestamp(as_of or episode.timestamp_decision)
    eligible = [event for event in events if _timestamp(event["event_time"]) <= boundary]
    observations = [event for event in eligible if event.get("event_type") == "market_input_observed" and isinstance(event.get("payload"), dict)]
    if not observations:
        raise ValueError("historical replay requires an eligible market_input_observed event")
    observation = observations[-1]
    market_payload = observation["payload"].get("market", observation["payload"])
    market = MarketInput.model_validate(market_payload)
    belief = build_belief_state(market, source_event_id=observation["event_id"])
    fair_probability = float(episode.forecasts.get("fair_probability") or market.price)
    confidence = float(episode.uncertainty.get("confidence") or 0.0)
    uncertainty = float(episode.uncertainty.get("model_uncertainty") or 0.0)
    if mode == "stress": uncertainty = min(1.0, uncertainty * 1.5 + .05)
    policy = ObjectivePolicy.model_validate(episode.available_information.get("objective_policy") or {}) if mode == "historical" else objective_policy or ObjectivePolicy()
    evaluations = evaluate_actions(market, fair_probability=fair_probability, confidence=confidence, uncertainty=uncertainty, objective_policy=policy)
    preferred_side = episode.selected_action.get("side") or "YES"
    proposal = propose_action(evaluations, preferred_side)
    return {
        "replay_id": f"replay_{episode.episode_id}_{mode}_{boundary.isoformat()}",
        "episode_id": episode.episode_id,
        "mode": mode,
        "as_of": boundary.isoformat().replace("+00:00", "Z"),
        "eligible_event_ids": [event["event_id"] for event in eligible],
        "lookahead_excluded": sum(1 for event in events if _timestamp(event["event_time"]) > boundary),
        "belief_state": belief.model_dump(mode="json"),
        "action_evaluations": [item.model_dump(mode="json") for item in evaluations],
        "policy_proposal": proposal.model_dump(mode="json"),
        "incumbent_action": episode.selected_action,
        "versions": {
            "objective_policy": episode.objective_policy_version,
            "policy": episode.policy_version,
            "risk_policy": episode.risk_policy_version,
            "data": episode.data_versions,
            "replayed_objective_policy": policy.version,
        },
        "mismatch": proposal.proposed_side != episode.selected_action.get("side") or proposal.proposed_action != episode.selected_action.get("action"),
    }