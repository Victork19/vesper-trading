"""Paper-only strategy tournament and forecast combination helpers."""
import math
import os


def _clamp(value, lower=.01, upper=.99):
    return max(lower, min(upper, float(value)))


def _logit(value):
    value = _clamp(value, .001, .999)
    return math.log(value / (1 - value))


def _sigmoid(value):
    return 1 / (1 + math.exp(-max(-30, min(30, value))))


def _skill(memory, strategy_ids):
    samples = []
    for decision in memory.decisions():
        if decision.strategy_id not in strategy_ids or not decision.research_eligible:
            continue
        if decision.outcome not in ('win', 'loss', 'push') or decision.resolved_yes is None:
            continue
        probability = decision.model_probability if decision.model_probability is not None else decision.fair_probability
        samples.append((_clamp(probability), 1.0 if decision.resolved_yes else 0.0))
    minimum = max(3, int(os.getenv('AUTO_PAPER_ENSEMBLE_MIN_SKILL_SAMPLES', '5')))
    if len(samples) < minimum:
        return None
    brier = sum((probability - outcome) ** 2 for probability, outcome in samples) / len(samples)
    return {'samples': len(samples), 'brier': brier, 'skill': max(.05, 1 - brier)}


def adaptive_fast_weight(memory):
    """Use resolved paper Brier scores only after the minimum sample exists."""
    configured = max(.1, min(.9, float(os.getenv('AUTO_PAPER_ENSEMBLE_FAST_WEIGHT', '.6'))))
    fast = _skill(memory, {'fast_model'})
    baseline = _skill(memory, {'reference_class'})
    if not fast or not baseline:
        return configured, {'method': 'configured', 'fast': fast, 'baseline': baseline}
    # Keep the configured prior as the center point.  Resolved Brier skill
    # nudges the weight toward the better research-eligible model without
    # allowing a small sample to completely replace the operator setting.
    weight = configured + .5 * (fast['skill'] - baseline['skill'])
    return max(.1, min(.9, weight)), {'method': 'resolved_brier_adaptive', 'fast': fast, 'baseline': baseline}


def hybrid_forecast(baseline, fast, memory):
    """Combine baseline and independent fast forecasts in log-odds space."""
    weight, adaptation = adaptive_fast_weight(memory)
    baseline_probability = _clamp(baseline['probability'])
    fast_probability = _clamp(fast['probability'])
    probability = _sigmoid((1 - weight) * _logit(baseline_probability) + weight * _logit(fast_probability))
    disagreement = abs(baseline_probability - fast_probability)
    uncertainty = min(.48, (1 - weight) * float(baseline.get('uncertainty', .25)) + weight * float(fast.get('uncertainty', .25)) + disagreement * .25)
    return {
        'model_version': 'hybrid_ensemble_v1',
        'probability': probability,
        'raw_probability': probability,
        'lower_bound': _clamp(probability - uncertainty),
        'upper_bound': _clamp(probability + uncertainty),
        'uncertainty': uncertainty,
        'confidence': max(.05, min(.9, 1 - uncertainty)),
        'calibration_samples': min(int(baseline.get('calibration_samples', 0)), int(fast.get('calibration_samples', 0))),
        'calibration_status': 'warming',
        'components': [
            {'strategy_id': 'reference_class', 'model_version': baseline.get('model_version'), 'probability': baseline_probability, 'weight': round(1 - weight, 6)},
            {'strategy_id': 'fast_model', 'model_version': fast.get('model_version'), 'probability': fast_probability, 'weight': round(weight, 6)},
        ],
        'weighting': adaptation,
    }
