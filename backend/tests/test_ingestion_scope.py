from app.ingestion import IngestionRunner, IngestionStore


class FakeMarketData:
 def __init__(self):
  self.calls=[]
 def markets(self,limit,**kwargs):
  self.calls.append((limit,kwargs))
  return []


def test_ingestion_uses_fast_research_window_by_default(monkeypatch):
 monkeypatch.setenv('FAST_MARKETS_ONLY','true')
 monkeypatch.setenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05')
 monkeypatch.setenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')
 runner=object.__new__(IngestionRunner);runner.data=FakeMarketData()
 runner.research_markets(50)
 limit,params=runner.data.calls[0]
 assert limit==50 and params['order']=='endDate' and params['ascending'] is True and params['closed'] is False
 assert params['end_date_min']<params['end_date_max']


def test_ingestion_can_opt_out_of_fast_market_scope(monkeypatch):
 monkeypatch.setenv('FAST_MARKETS_ONLY','false')
 runner=object.__new__(IngestionRunner);runner.data=FakeMarketData()
 runner.research_markets(25)
 assert runner.data.calls==[(25,{})]


def test_string_order_book_flag_is_classified_as_ineligible():
 store=object.__new__(IngestionStore)
 assert store.eligibility_reason({'enableOrderBook':'false'})=='order_book_disabled'


def test_ingestion_accepts_ask_only_books_for_buy_execution():
 store=object.__new__(IngestionStore);store.require_books=True;store.require_both_books=True
 market={'question':'Will Bitcoin go up?','outcomePrices':['.6','.4'],'active':True,'closed':False,'outcomes':['Yes','No'],'clobTokenIds':['yes','no'],'_vesper_book':{'best_ask':.6,'observed_at':'2026-08-21T00:00:00Z'},'_vesper_no_book':{'best_ask':.4,'observed_at':'2026-08-21T00:00:00Z'}}
 assert store.validation_reason(market) is None
