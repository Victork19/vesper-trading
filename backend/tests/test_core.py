import os
import pytest
from pydantic import ValidationError
from app.models import BookLevel, OrderBook
from fastapi.testclient import TestClient
from app.main import app
from app.main import memory as trading_memory
from app.resolver import OutcomeResolver
from app.settlement import parse_terminal_resolution
c=TestClient(app,headers={'X-Vesper-Key':'client-test-key'})
admin={'X-Vesper-Key':'admin-test-key'}
def test_health(): assert c.get('/health').json()['memory_load_bearing'] is True
def test_paper_decision_and_deletion():
 payload={'market':{'market_id':'m1','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60},'strategy_id':'reference_class'}
 before=c.post('/decide',json=payload).json();assert before['action']!='DO NOTHING'
 c.post('/demo/clear-learning',headers=admin);after=c.post('/decide',json=payload).json();assert after['action']!='DO NOTHING'

def test_failure_changes_future_decision():
 payload={'market':{'market_id':'m2','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60},'strategy_id':'reference_class'}
 first=c.post('/decide',json=payload).json()
 c.post('/outcomes',json={'decision_id':first['id'],'outcome':'loss','pnl':-1,'clv':-.05})
 second=c.post('/decide',json=payload).json()
 assert second['cited_scars']
 assert second['action']=='DO NOTHING'

def test_live_mode_fails_closed():
 response=c.post('/mode/live',headers=admin)
 assert response.status_code==403

def test_readiness_has_explicit_checks():
 body=c.get('/ready').json()
 assert 'checks' in body and 'data_quality' in body['checks']

def test_no_side_is_a_buy_not_a_sell():
 payload={'market':{'market_id':'no-side','question':'Will event resolve yes?','price':.70,'liquidity':25000,'volume_24h':100000,'reference_rate':.40},'strategy_id':'reference_class'}
 body=c.post('/decide',json=payload).json()
 assert body['action']=='BUY' and body['side']=='NO' and body['size']>0

def test_outcome_is_idempotent_and_terminal():
 payload={'market':{'market_id':'idempotency','question':'Will event resolve yes?','price':.45,'liquidity':25000,'volume_24h':100000,'reference_rate':.60},'strategy_id':'reference_class'}
 decision=c.post('/decide',json=payload).json()
 assert c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'loss','pnl':-1}).status_code==200
 assert c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'loss','pnl':-1}).status_code==409

def test_learning_reset_is_serializable():
 assert c.post('/demo/clear-learning',headers=admin).status_code==200

def test_settlement_uses_contract_payoff_when_resolution_is_known():
 payload={'market':{'market_id':'known-resolution','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70},'strategy_id':'reference_class'}
 decision=c.post('/decide',json=payload).json()
 response=c.post('/outcomes',json={'decision_id':decision['id'],'outcome':'win','pnl':999,'resolved_yes':True})
 assert response.status_code==200
 assert response.json()['pnl']<1

def test_paper_execution_creates_durable_order_record():
 payload={'market':{'market_id':'paper-order','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70},'strategy_id':'reference_class','execute':True}
 response=c.post('/decide',json=payload)
 assert response.status_code==200
 body=response.json()
 assert body['order_id']
 order=c.get('/orders/'+body['order_id']).json()
 assert order['status']=='filled' and order['filled_size']==body['size']

def test_resolver_settles_only_unambiguous_terminal_market():
 class FakeData:
  def market(self,market_id):
   if market_id!='resolver-market': raise RuntimeError('unrelated test market')
   return {'id':market_id,'closed':True,'resolved':True,'outcomes':['Yes','No'],'outcomePrices':['1','0']}
 payload={'market':{'market_id':'resolver-market','question':'Will event resolve yes?','price':.40,'liquidity':25000,'volume_24h':100000,'reference_rate':.70},'strategy_id':'reference_class','execute':True}
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
