"""Post-settlement process attribution for EDA episodes."""

from __future__ import annotations

from typing import Any, Iterable

from .models import ActionEvaluation, DecisionRecord


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def evaluate_attribution(
    decision: DecisionRecord,
    *,
    action_evaluations: Iterable[ActionEvaluation | dict[str, Any]] = (),
) -> dict[str, Any]:
    """Attribute observed process error without asserting causal certainty."""
    evaluations = [
        item if isinstance(item, ActionEvaluation) else ActionEvaluation.model_validate(item)
        for item in action_evaluations
    ]
    outcome_value = 1.0 if decision.resolved_yes else 0.0 if decision.resolved_yes is not None else None
    forecast_error = abs(float(decision.fair_probability) - outcome_value) if outcome_value is not None else None
    observed_action = f"BUY {decision.side}" if decision.action == "BUY" and decision.side else "DO NOTHING"
    selected = next((item for item in evaluations if item.action == observed_action), None)
    alternatives = [item for item in evaluations if item.action != observed_action and item.available]
    best_alternative = max((item.expected_utility for item in alternatives), default=0.0)
    selected_utility = selected.expected_utility if selected else 0.0
    policy_error = _clamp(max(0.0, best_alternative - selected_utility) / (abs(best_alternative) + 1e-9)) if alternatives else 0.0
    expected_price = decision.executable_price or decision.price
    actual_price = decision.executed_average_price or decision.paper_execution_price
    execution_error = _clamp(abs(float(actual_price) - float(expected_price))) if actual_price is not None and decision.executed_size > 0 else 0.0
    expected_size = max(0.0, float(decision.size))
    actual_size = float(decision.executed_size or expected_size * decision.paper_fill_fraction)
    realized_unit_pnl = float(decision.pnl) / actual_size if actual_size > 0 else float(decision.pnl)
    return {
        "status": "observational",
        "categories": {
            "forecast_error": forecast_error,
            "calibration_error": forecast_error,
            "consequence_model_error": _clamp(abs(realized_unit_pnl - selected_utility) / (abs(realized_unit_pnl) + abs(selected_utility) + 1e-9)) if selected else 0.0,
            "policy_error": policy_error,
            "risk_sizing_error": _clamp(abs(actual_size - expected_size) / (expected_size + 1e-9)),
            "execution_error": execution_error,
            "data_quality_error": _clamp(1.0 - float(decision.quality_score)),
            "environmental_shock": _clamp(max(0.0, (forecast_error or 0.0) - 0.5) * 2.0),
            "ordinary_variance": _clamp((forecast_error or 0.0) * (1.0 - _clamp(max(0.0, (forecast_error or 0.0) - 0.5) * 2.0))),
        },
        "metrics": {
            "predicted_probability": decision.fair_probability,
            "realized_probability": outcome_value,
            "realized_pnl": decision.pnl,
            "realized_unit_pnl": realized_unit_pnl,
            "clv": decision.clv,
            "selected_expected_utility": selected_utility,
            "best_available_alternative_utility": best_alternative,
            "expected_size": expected_size,
            "realized_size": actual_size,
            "expected_execution_price": expected_price,
            "realized_execution_price": actual_price,
        },
    }