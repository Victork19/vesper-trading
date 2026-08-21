from app.fast_probability import estimate_from_closes
from app.market_data import PolymarketData

def test_fast_probability_returns_bounded_directional_estimate():
 result=estimate_from_closes([100+i*.02 for i in range(40)],5,'up')
 assert result and .05<=result['probability']<=.95 and result['model_version']=='fast_market_v1'

def test_fast_probability_requires_enough_history():
 assert estimate_from_closes([100,100.1,100.2],5,'up') is None

def test_market_input_preserves_expiry_and_paper_costs(monkeypatch):
 monkeypatch.setenv('PAPER_FEE_RATE','.02');monkeypatch.setenv('PAPER_SLIPPAGE_BPS','10')
 item={'id':'fast-1','question':'Bitcoin Up or Down?','outcomePrices':'["0.5","0.5"]','endDate':'2099-01-01T00:00:00Z','active':True,'closed':False,'liquidity':10000,'volume24hr':10000}
 market=PolymarketData().to_input(item)
 assert market.market_end_time and market.resolution_hours>0 and market.fee_rate==.02 and market.slippage_bps==10
