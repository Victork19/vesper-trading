from app.fast_probability import estimate_from_closes
from app.market_data import PolymarketData
from app.fast_probability import _fresh_candles
from app.settlement import parse_terminal_resolution
from app.adapters import paper_fill_profile, paper_execution_profile
from app.models import BookLevel, OrderBook, MarketInput
from app.market_data import PolymarketData
from app.market_policy import fast_market_allowed
from app.models import DecisionRecord, Mode
from app.fast_probability import FastMarketProbability
from datetime import datetime, timezone, timedelta

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

def test_paper_fill_model_fails_closed_without_depth():
 class Market:
  book_asks=[]
 assert paper_fill_profile(Market(),.02)==(0.0,'no_depth_reported_no_fill')

def test_paper_fill_model_never_uses_wrong_contract_book():
 class Market:
  book_asks=[BookLevel(price=.4,size=.02)]
  yes_book_asks=[BookLevel(price=.4,size=.02)]
  no_book_asks=[]
  quality_score=1.0
 assert paper_fill_profile(Market(),.01,'NO')==(0.0,'no_depth_reported_no_fill')

def test_paper_execution_walks_depth_and_returns_vwap_price():
 class Market:
  book_asks=[BookLevel(price=.4,size=.01),BookLevel(price=.5,size=.01)]
  quality_score=1.0
  fee_rate=0.0
  slippage_bps=0.0
 profile=paper_execution_profile(Market(),.015)
 assert profile['fill_fraction']==1 and abs(profile['average_quote_price']-(.004+.0025)/.015)<1e-9
 assert profile['execution_price']==profile['average_quote_price']

def test_paper_execution_marks_partial_depth_as_partial_fill():
 from app.adapters import PaperExecution
 decision=DecisionRecord(id='partial',mode=Mode.PAPER,market_id='m',strategy_id='s',action='BUY',side='YES',size=.02,price=.4,fair_probability=.7,confidence=.8,risk_score=5,edge=.2,rationale='test',paper_fill_fraction=.5,paper_execution_price=.41)
 assert PaperExecution().execute(decision)['status']=='partially_filled'

def test_paper_execution_preserves_zero_price_without_fallback():
 from app.adapters import PaperExecution
 decision=DecisionRecord(id='zero-price',mode=Mode.PAPER,market_id='m',strategy_id='s',action='BUY',side='YES',size=.02,price=.4,fair_probability=.7,confidence=.8,risk_score=5,edge=.2,rationale='test',paper_fill_fraction=1,paper_execution_price=0,executable_price=.4)
 assert PaperExecution().execute(decision)['average_fill_price']==0

def test_process_metrics_do_not_mix_model_versions():
 from app.metrics import MetricsEngine
 class Memory:
  def __init__(self): self.items=[]
  def snapshots(self):
   from app.models import ProcessSnapshot
   return [ProcessSnapshot.model_validate(item) for item in self.items]
  def put(self,tier,key,value):
   self.items=[item for item in self.items if item.get('strategy_id')!=value['strategy_id'] or item.get('market_type')!=value['market_type'] or item.get('regime')!=value['regime'] or item.get('model_version')!=value.get('model_version')]
   self.items.append(value)
  def event(self,*args): pass
 memory=Memory();engine=MetricsEngine(memory)
 base=dict(mode=Mode.PAPER,market_id='m',strategy_id='s',market_type='crypto',regime='range',action='BUY',side='YES',size=.01,price=.4,fair_probability=.6,confidence=.8,risk_score=5,edge=.2,rationale='test',outcome='win')
 engine.outcome(DecisionRecord(id='v1',model_version='fast_market_v1',**base),.1,.01)
 engine.outcome(DecisionRecord(id='v2',model_version='fast_market_v2',**base),-.1,-.01)
 assert {item['model_version'] for item in memory.items}=={'fast_market_v1','fast_market_v2'}

def test_fast_market_policy_rejects_slow_polymarket_exposure(monkeypatch):
 monkeypatch.setenv('FAST_MARKETS_ONLY','true');monkeypatch.setenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')
 assert fast_market_allowed(.25,'polymarket-clob')
 assert not fast_market_allowed(2,'polymarket-clob')
 assert fast_market_allowed(168,'manual')

def test_fast_calibration_excludes_future_decisions():
 class Memory:
  def decisions(self):
   return [DecisionRecord(id='future-created',created_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat().replace('+00:00','Z'),mode=Mode.PAPER,market_id='m',strategy_id='reference_class',action='BUY',price=.4,fair_probability=.7,confidence=.7,risk_score=5,edge=.2,rationale='test',model_version='fast_market_v3',raw_model_probability=.7,resolved_yes=True,resolved_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),outcome='win'),DecisionRecord(id='future-resolved',created_at=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat().replace('+00:00','Z'),mode=Mode.PAPER,market_id='m2',strategy_id='reference_class',action='BUY',price=.4,fair_probability=.7,confidence=.7,risk_score=5,edge=.2,rationale='test',model_version='fast_market_v3',raw_model_probability=.7,resolved_yes=True,resolved_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat().replace('+00:00','Z'),outcome='win')]
 model=FastMarketProbability()
 assert model._calibration_samples(Memory(),'fast_market_v3')==[]

def test_fast_calibration_excludes_temporally_invalid_decisions():
 class Memory:
  def decisions(self):
   return [DecisionRecord(id='invalid-order',created_at='2026-08-21T01:00:00Z',mode=Mode.PAPER,market_id='m',strategy_id='reference_class',action='BUY',price=.4,fair_probability=.7,confidence=.7,risk_score=5,edge=.2,rationale='test',model_version='fast_market_v3',raw_model_probability=.7,resolved_yes=True,resolved_at='2026-08-21T00:00:00Z',outcome='win')]
 assert FastMarketProbability()._calibration_samples(Memory(),'fast_market_v3')==[]

def test_market_input_keeps_independent_yes_and_no_books():
 item={'id':'dual-book','question':'Will Bitcoin go up?','outcomes':['Yes','No'],'clobTokenIds':['yes-token','no-token'],'outcomePrices':['.6','.4'],'active':True,'liquidity':10000,'volume24hr':10000}
 yes=OrderBook(token_id='yes-token',observed_at='2026-08-21T00:00:00Z',bids=[BookLevel(price=.5,size=.02)],asks=[BookLevel(price=.6,size=.02)],best_bid=.5,best_ask=.6)
 no=OrderBook(token_id='no-token',observed_at='2026-08-21T00:00:00Z',bids=[BookLevel(price=.3,size=.01)],asks=[BookLevel(price=.4,size=.01)],best_bid=.3,best_ask=.4)
 market=PolymarketData().to_input(item,yes_book=yes,no_book=no)
 assert market.yes_token_id=='yes-token' and market.no_token_id=='no-token'
 assert market.yes_ask==.6 and market.no_ask==.4 and market.no_book_asks[0].size==.01
 assert paper_fill_profile(market,.02,'NO')[0]==.5

def test_market_input_measures_contract_quote_skew():
 item={'id':'skewed-books','question':'Will Bitcoin go up?','outcomes':['Yes','No'],'clobTokenIds':['yes-token','no-token'],'outcomePrices':['.6','.4'],'active':True,'liquidity':10000,'volume24hr':10000}
 yes=OrderBook(token_id='yes-token',observed_at='2026-08-21T00:00:00Z',asks=[BookLevel(price=.6,size=.02)],best_ask=.6)
 no=OrderBook(token_id='no-token',observed_at='2026-08-21T00:00:20Z',asks=[BookLevel(price=.4,size=.02)],best_ask=.4)
 market=PolymarketData().to_input(item,yes_book=yes,no_book=no)
 assert market.quote_skew_seconds==20 and market.quote_observed_at.isoformat().startswith('2026-08-21T00:00:00')

def test_book_uses_venue_timestamp_for_coherence():
 data=PolymarketData()
 observed=data._observed_at({'timestamp':1700000000000})
 assert observed=='2023-11-14T22:13:20Z'

def test_market_input_rejects_invalid_model_interval():
 import pytest
 with pytest.raises(ValueError):MarketInput(market_id='bad-interval',question='test',price=.5,model_probability=.8,model_lower_bound=.1,model_upper_bound=.7)

def test_decision_record_rejects_invalid_model_interval():
 import pytest
 with pytest.raises(ValueError):DecisionRecord(id='bad-decision',mode=Mode.PAPER,market_id='m',strategy_id='s',action='DO NOTHING',price=.5,fair_probability=.5,confidence=.5,risk_score=1,edge=0,rationale='test',model_probability=.8,model_lower_bound=.1,model_upper_bound=.7)
