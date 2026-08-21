import os
import pytest
from pydantic import ValidationError
from app.models import BookLevel, OrderBook, OrderStatus, DecisionRecord, Mode, MarketInput
from fastapi.testclient import TestClient
from app.main import app, _research_slice, live_evidence_checks
from app.main import memory as trading_memory
from app.resolver import OutcomeResolver
from app.settlement import parse_terminal_resolution
c=TestClient(app,headers={'X-Vesper-Key':'client-test-key'})
admin={'X-Vesper-Key':'admin-test-key'}
def paper_books():
 return {'book_asks':[{'price':.4,'size':1}], 'yes_book_asks':[{'price':.4,'size':1}], 'no_book_asks':[{'price':.4,'size':1}]}
def test_health(): assert c.get('/health').json()['memory_load_bearing'] is True
def test_paper_decision_and_deletion():
 payload={'market':{'market_id':'m1','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60,**paper_books()},'strategy_id':'reference_class'}
 before=c.post('/decide',json=payload).json();assert before['action']!='DO NOTHING'
 c.post('/demo/clear-learning',headers=admin);after=c.post('/decide',json=payload).json();assert after['action']!='DO NOTHING'

def test_failure_changes_future_decision():
 payload={'market':{'market_id':'m2','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60,**paper_books()},'strategy_id':'reference_class'}
 first=c.post('/decide',json=payload).json()
 c.post('/outcomes',json={'decision_id':first['id'],'outcome':'loss','pnl':-1,'clv':-.05})
 second=c.post('/decide',json=payload).json()
 assert second['cited_scars']
 assert second['action']=='DO NOTHING'

def test_live_mode_fails_closed():
 response=c.post('/mode/live',headers=admin)
 assert response.status_code==403

def test_live_evidence_gate_fails_without_oos_proof():
 assert not all(live_evidence_checks().values())

def test_live_evidence_gate_accepts_only_positive_oos_proof(monkeypatch):
 import app.main as main_module
 monkeypatch.setattr(main_module,'research_report',lambda _: {'status':'available','out_of_sample':{'count':10,'expectancy':.01,'expectancy_ci_low':.001,'brier_lift_vs_market':.02,'brier_lift_ci_low':.001,'log_loss_lift_vs_market':.01,'log_loss_lift_ci_low':.001}})
 assert all(main_module.live_evidence_checks().values())

def test_readiness_has_explicit_checks():
 body=c.get('/ready').json()
 assert 'checks' in body and 'data_quality' in body['checks']

def test_readiness_returns_service_unavailable_when_failed(monkeypatch):
 import app.main as main_module
 monkeypatch.setattr(main_module,'_readiness_payload',lambda: {'ready':False,'checks':{'memory':False}})
 response=c.get('/ready')
 assert response.status_code==503 and response.json()['ready'] is False

def test_no_side_is_a_buy_not_a_sell():
 payload={'market':{'market_id':'no-side','question':'Will event resolve yes?','price':.70,'liquidity':25000,'volume_24h':100000,'reference_rate':.40,**paper_books()},'strategy_id':'reference_class'}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='BUY' and body['side']=='NO' and body['size']>0

def test_polymarket_without_reference_evidence_is_a_no_trade():
 payload={'market':{'market_id':'unsupported-edge','question':'Unmodeled market','price':.04,'liquidity':25000,'volume_24h':100000,'source':'polymarket-clob'},'strategy_id':'reference_class','execute':True}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='DO NOTHING' and body['size']==0 and 'reference_evidence_required' in body['gates']

def test_polymarket_requires_independent_yes_and_no_books():
 payload={'market':{'market_id':'dual-book-gate','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.70,'source':'polymarket-clob','yes_ask':.46,'no_ask':.56},'strategy_id':'reference_class'}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='DO NOTHING' and 'both_contract_books_required' in body['gates']

def test_polymarket_rejects_incoherent_contract_books():
 payload={'market':{'market_id':'skew-gate','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.70,'source':'polymarket-clob','yes_ask':.46,'no_ask':.56,'quote_skew_seconds':11,'yes_book_asks':[{'price':.46,'size':1}],'no_book_asks':[{'price':.56,'size':1}]},'strategy_id':'reference_class'}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='DO NOTHING' and 'incoherent_contract_books' in body['gates']

def test_autonomous_cycle_uses_latest_decision_for_cooldown():
 from app.worker import autonomous_paper_cycle
 from app.models import HotState
 from datetime import datetime, timezone, timedelta
 now=datetime.now(timezone.utc)
 class Memory:
  def hot(self): return HotState(mode=Mode.PAPER)
  def decisions(self):
   base=dict(mode=Mode.PAPER,market_id='cooldown-market',strategy_id='s',action='BUY',side='YES',size=.01,price=.4,fair_probability=.6,confidence=.8,risk_score=5,edge=.2,rationale='test',outcome='win',source='polymarket-clob')
   return [DecisionRecord(id='new',created_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),**base),DecisionRecord(id='old',created_at=(now-timedelta(days=2)).isoformat().replace('+00:00','Z'),**base)]
 class Data:
  def markets(self,*args,**kwargs): return [{'id':'cooldown-market','question':'Will Bitcoin go up?','endDate':(now+timedelta(minutes=10)).isoformat(),'active':True,'closed':False,'category':'crypto','reference_rate':.6}]
  def token_pair(self,item): return 'yes','no'
  def book(self,token): return None
  def to_input(self,item,**kwargs): return MarketInput(market_id='cooldown-market',question=item['question'],market_type='crypto',price=.4,reference_rate=.6,source='polymarket-clob',resolution_hours=.16,quality_score=1,market_status='active')
 class Store:
  def save_verified_input(self,market_input): return market_input.snapshot_hash
 class Runner: data=Data();store=Store()
 result=autonomous_paper_cycle(Runner(),Memory(),lambda request,auth: None)
 assert result['evaluated']==0 and result['skipped']==1

def test_worker_failure_backoff_is_bounded_and_resets():
 from app.worker import _failure_backoff_seconds
 assert _failure_backoff_seconds(60,0)==60
 assert _failure_backoff_seconds(60,3)==480
 assert _failure_backoff_seconds(60,10,300)==300

def test_outcome_is_idempotent_and_terminal():
 payload={'market':{'market_id':'idempotency','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60,**paper_books()},'strategy_id':'reference_class'}
 decision=c.post('/decide',json=payload).json()
 response=c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'loss','pnl':-1})
 assert response.status_code==200 and response.json()['resolved_at']
 assert c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'loss','pnl':-1}).status_code==409

def test_learning_reset_is_serializable():
 assert c.post('/demo/clear-learning',headers=admin).status_code==200

def test_settlement_uses_contract_payoff_when_resolution_is_known():
 payload={'market':{'market_id':'known-resolution','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70,**paper_books()},'strategy_id':'reference_class'}
 decision=c.post('/decide',json=payload).json()
 response=c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'win','pnl':999,'resolved_yes':True})
 assert response.status_code==200
 assert response.json()['pnl']<1

def test_paper_execution_creates_durable_order_record():
 payload={'market':{'market_id':'paper-order','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70,**paper_books()},'strategy_id':'reference_class','execute':True}
 response=c.post('/decide',json=payload)
 assert response.status_code==200
 body=response.json()
 assert body['order_id']
 order=c.get('/orders/'+body['order_id']).json()
 assert order['status']=='rejected' and order['filled_size']==0
 with pytest.raises(ValueError):trading_memory.save_order(trading_memory.order(body['order_id']).model_copy(update={'status':OrderStatus.ACCEPTED}))

def test_paper_decision_fails_closed_when_no_depth_is_available():
 payload={'market':{'market_id':'no-depth','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70},'strategy_id':'reference_class'}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='DO NOTHING' and body['size']==0 and 'paper_no_fill' in body['gates']

def test_resolver_settles_only_unambiguous_terminal_market():
 class FakeData:
  def market(self,market_id):
   if market_id!='resolver-market': raise RuntimeError('unrelated test market')
   return {'id':market_id,'closed':True,'resolved':True,'outcomes':['Yes','No'],'outcomePrices':['1','0']}
 payload={'market':{'market_id':'resolver-market','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70,**paper_books()},'strategy_id':'reference_class','execute':True}
 decision=c.post('/decide',json=payload).json()
 result=OutcomeResolver(memory=trading_memory,data=FakeData()).tick()
 assert result['settled']==1
 settled=next(item for item in trading_memory.decisions() if item.id==decision['id'])
 assert settled.outcome=='win' and settled.resolved_yes is True
 assert OutcomeResolver(memory=trading_memory,data=FakeData()).tick()['settled']==0

def test_resolver_ignores_closed_market_without_terminal_prices():
 assert parse_terminal_resolution({'closed':True,'outcomes':['Yes','No'],'outcomePrices':['0.5','0.5']}) is None

def test_crossed_order_book_is_rejected():
 with pytest.raises(ValidationError):
  OrderBook(token_id='t',observed_at='2026-08-19T00:00:00Z',bids=[BookLevel(price=.7,size=1)],asks=[BookLevel(price=.6,size=1)],best_bid=.7,best_ask=.6)

def test_research_reports_incremental_lift_against_market_baseline():
 items=[]
 for index,resolved in enumerate((True,True,False,True)):
  items.append(DecisionRecord(id=f'research-{index}',mode=Mode.PAPER,market_id=f'market-{index}',strategy_id='test',action='BUY',price=.5,fair_probability=.75 if resolved else .25,confidence=.8,risk_score=5,edge=.2,rationale='test',resolved_yes=resolved,outcome='win' if resolved else 'loss',pnl=.1 if resolved else -.1))
 report=_research_slice(items)
 assert report['market_brier'] is not None and report['brier_lift_vs_market'] is not None and report['brier_lift_ci_low'] is not None and report['expectancy_ci_low'] is not None

def test_research_scores_versioned_model_probability_not_downstream_fair_value():
 item=DecisionRecord(id='model-probability',mode=Mode.PAPER,market_id='model-market',strategy_id='test',action='BUY',price=.5,fair_probability=.51,model_version='fast_market_v3',model_probability=.9,confidence=.8,risk_score=5,edge=.2,rationale='test',resolved_yes=False,outcome='loss',pnl=-.1)
 report=_research_slice([item])
 assert report['brier']==pytest.approx(.81)

def test_research_report_does_not_pool_model_versions(monkeypatch):
 import app.main as main_module
 monkeypatch.setenv('FAST_MARKETS_ONLY','false')
 base=dict(mode=Mode.PAPER,strategy_id='version-isolation',market_type='crypto',regime='range',action='BUY',side='YES',size=.01,price=.4,fair_probability=.7,confidence=.8,risk_score=5,edge=.2,rationale='test',outcome='win',pnl=.1,paper_fill_fraction=1,market_context={'resolution_hours':.25})
 older=DecisionRecord(id='old-version',market_id='same-outcome-v1',model_version='fast_market_v2',created_at='2020-01-01T00:00:00Z',resolved_yes=True,**base)
 current=DecisionRecord(id='current-version',market_id='same-outcome-v2',model_version='fast_market_v3',created_at='2099-01-01T00:00:00Z',resolved_yes=False,outcome='loss',pnl=-.1,**{key:value for key,value in base.items() if key not in ('outcome','pnl')})
 report=main_module.research_report([older,current])
 assert report['model_version']=='fast_market_v3' and report['resolved_exposures']==1

def test_research_report_exposure_weights_repeated_market_predictions(monkeypatch):
 import app.main as main_module
 monkeypatch.setenv('FAST_MARKETS_ONLY','false')
 base=dict(mode=Mode.PAPER,strategy_id='weighted',market_type='crypto',regime='range',action='BUY',side='YES',price=.5,fair_probability=.5,confidence=.8,risk_score=5,edge=.2,rationale='test',model_version='fast_market_v3',paper_fill_fraction=1,market_context={'resolution_hours':.25},resolved_yes=False,outcome='loss',pnl=-.1)
 first=DecisionRecord(id='weighted-1',market_id='same-market',size=.01,model_probability=.2,created_at='2020-01-01T00:00:00Z',**base)
 second=DecisionRecord(id='weighted-2',market_id='same-market',size=.03,model_probability=.8,created_at='2020-01-01T00:01:00Z',**base)
 report=main_module.research_report([first,second])
 bin_item=next(item for item in report['calibration_bins'] if item['count']==1)
 assert report['independent_buckets']==1 and bin_item['predicted']==pytest.approx(.65)

def test_verified_market_input_binds_hash_to_exact_payload():
 from app.main import ingestion_store
 market=MarketInput(market_id='verified',question='test',price=.4,source='polymarket-clob',snapshot_hash='snapshot-verified')
 ingestion_store.save_verified_input(market)
 assert ingestion_store.verified_input_matches(market)
 enriched=market.model_copy(update={'model_version':'fast_market_v3','raw_model_probability':.6,'model_probability':.61,'reference_rate':.61,'regime':'trend','signals':{'fast_model':.61}})
 assert ingestion_store.verified_input_matches(enriched)
 altered=market.model_copy(update={'price':.41})
 assert not ingestion_store.verified_input_matches(altered)
 with pytest.raises(ValueError):ingestion_store.save_verified_input(altered)
