from app.fast_probability import estimate_from_closes
from app.market_data import PolymarketData
from app.fast_probability import _fresh_candles
from app.settlement import parse_terminal_resolution
from app.adapters import paper_fill_profile
from app.models import BookLevel
from app.market_policy import fast_market_allowed

def test_fast_probability_returns_bounded_directional_estimate():
 result=estimate_from_closes([100+i*.02 for i in range(40)],5,'up')
 assert result and .05<=result['probability']<=.95 and result['model_version']=='fast_market_v3' and result['lower_bound']<=result['probability']<=result['upper_bound']

def test_fast_probability_requires_enough_history():
 assert estimate_from_closes([100,100.1,100.2],5,'up') is None

def test_fast_probability_is_uncertain_on_flat_prices():
 result=estimate_from_closes([100.0]*40,5,'up')
 assert result and abs(result['probability']-.5)<.02 and result['confidence']<.85 and result['regime'] in ('range','mean_reversion')

def test_market_input_preserves_expiry_and_paper_costs(monkeypatch):
 monkeypatch.setenv('PAPER_FEE_RATE','.02');monkeypatch.setenv('PAPER_SLIPPAGE_BPS','10')
 item={'id':'fast-1','question':'Bitcoin Up or Down?','outcomePrices':'["0.5","0.5"]','endDate':'2099-01-01T00:00:00Z','active':True,'closed':False,'liquidity':10000,'volume24hr':10000}
 market=PolymarketData().to_input(item)
 assert market.market_end_time and market.resolution_hours>0 and market.fee_rate==.02 and market.slippage_bps==10

def test_stale_candle_series_is_rejected():
 assert not _fresh_candles([[0,0,0,0,100,0]])

def test_explicit_terminal_winner_is_supported():
 assert parse_terminal_resolution({'closed':True,'resolved':True,'finalOutcome':'Yes'}) is True

def test_paper_fill_model_is_depth_aware_and_replayable():
 class Market:
  book_asks=[BookLevel(price=.4,size=.01)]
  quality_score=1.0
 first=paper_fill_profile(Market(),.02)
 second=paper_fill_profile(Market(),.02)
 assert first==second and first[0]==.5

def test_fast_market_policy_rejects_slow_polymarket_exposure(monkeypatch):
 monkeypatch.setenv('FAST_MARKETS_ONLY','true');monkeypatch.setenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')
 assert fast_market_allowed(.25,'polymarket-clob')
 assert not fast_market_allowed(2,'polymarket-clob')
 assert fast_market_allowed(168,'manual')
