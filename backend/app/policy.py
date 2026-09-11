"""Deterministic policy selection between forecast preference and consequences."""

from __future__ import annotations

from .models import ActionEvaluation, PolicyProposal

POLICY_VERSION = "deterministic_policy_v2"


def propose_action(evaluations: list[ActionEvaluation], preferred_side: str) -> PolicyProposal:
    by_action = {item.action: item for item in evaluations}
    preferred_action = f"BUY {preferred_side}"
    preferred = by_action.get(preferred_action)
    alternatives = [
        {
            "action": item.action,
            "side": item.side,
            "available": item.available,
            "expected_utility": item.expected_utility,
            "expected_value_per_unit": item.expected_value_per_unit,
            "reasons": item.reasons,
        }
        for item in evaluations
    ]
    if preferred is None or not preferred.available:
        return PolicyProposal(policy_version=POLICY_VERSION, alternatives=alternatives, rationale="Forecast-preferred side was not executable; retain the no-action baseline.", status="rejected")
    if preferred.expected_utility <= 0 or preferred.expected_value_per_unit <= 0:
        return PolicyProposal(policy_version=POLICY_VERSION, alternatives=alternatives, rationale="Forecast-preferred side did not produce positive cost-adjusted consequence utility.", status="rejected")
    return PolicyProposal(policy_version=POLICY_VERSION, proposed_action="BUY", proposed_side=preferred_side, expected_utility=preferred.expected_utility, alternatives=alternatives, rationale="Forecast-preferred side has positive executable consequence utility; risk authorization remains required.", status="proposed")

