"""Opt-in experimental strategy: market-relative microstructure ensemble.

This is deliberately conservative. It may move the market prior only when a
model has calibration evidence and both contract books provide executable
depth. It is research code, not a live authorization mechanism.
"""
import math
from .models import MarketInput

MODEL_VERSION='relative_microstructure_v1'

def _clamp(value,lower=.01,upper=.99):return max(lower,min(upper,value))
def _logit(value):
 value=_clamp(value,.001,.999);return math.log(value/(1-value))
def _sigmoid(value):return 1/(1+math.exp(-max(-30,min(30,value))))
def _depth(levels):return sum(float(level.size) for level in (levels or [])[:5])

def estimate(market:MarketInput):
 if market.model_probability is None or market.model_calibration_samples<5:return None
 if not market.yes_book_bids or not market.no_book_bids:return None
 yes_depth=_depth(market.yes_book_bids);no_depth=_depth(market.no_book_bids);total=yes_depth+no_depth
 if total<=0:return None
 midpoint=(market.yes_ask+(1-market.no_ask))*.5 if market.yes_ask is not None and market.no_ask is not None else market.price
 reliability=market.model_calibration_samples/(market.model_calibration_samples+60)
 uncertainty=market.model_uncertainty if market.model_uncertainty is not None else .25
 model_weight=reliability*max(.1,1-min(.9,uncertainty))
 imbalance=(yes_depth-no_depth)/total
 # The imbalance term is intentionally tiny; it is a tie-breaker, not a
 # fabricated source of large directional edge.
 fair=_sigmoid((1-model_weight)*_logit(midpoint)+model_weight*_logit(market.model_probability)+.08*imbalance)
 fair=_clamp(fair)
 uncertainty=max(uncertainty,abs(fair-midpoint)*1.5,.08+abs(imbalance)*.08)
 return {'model_version':MODEL_VERSION,'probability':fair,'raw_probability':market.model_probability,'uncertainty':min(.48,uncertainty),'lower_bound':_clamp(fair-min(.48,uncertainty)), 'upper_bound':_clamp(fair+min(.48,uncertainty)),'calibration_samples':market.model_calibration_samples,'calibration_status':market.model_calibration_status,'reliability':reliability,'book_imbalance':imbalance,'market_midpoint':midpoint}
