from app.attribution import evaluate_attribution
from app.models import ActionEvaluation, DecisionRecord, Mode


def decision(**overrides):
    values = dict(
        id="decision-attribution",
        mode=Mode.PAPER,
        market_id="market-attribution",
        strategy_id="reference_class",
        action="BUY",
        side="YES",
        size=0.1,
        price=0.4,
        executable_price=0.42,
        fair_probability=0.6,
        confidence=0.8,
        risk_score=3,
        edge=0.18,
        rationale="test",
        outcome="loss",
        pnl=-0.04,
        resolved_yes=False,
        paper_fill_fraction=1.0,
        paper_execution_price=0.42,
        quality_score=0.9,
    )
    values.update(overrides)
    return DecisionRecord(**values)


def test_attribution_separates_forecast_and_counterfactual_metrics():
    report = evaluate_attribution(
        decision(),
        action_evaluations=[
            ActionEvaluation(action="DO NOTHING", expected_utility=0),
            ActionEvaluation(action="BUY YES", side="YES", expected_utility=0.03),
            ActionEvaluation(action="BUY NO", side="NO", expected_utility=-0.01),
        ],
    )

    assert report["status"] == "observational"
    assert report["categories"]["forecast_error"] == 0.6
    assert report["metrics"]["best_available_alternative_utility"] == 0
    assert report["metrics"]["selected_expected_utility"] == 0.03


def test_attribution_marks_unresolved_forecast_error_as_unknown():
    report = evaluate_attribution(decision(outcome="pending", resolved_yes=None, pnl=0))

    assert report["categories"]["forecast_error"] is None
    assert report["metrics"]["realized_probability"] is None