import os,re
from .models import MarketInput
from .research_validation import exposure_notional, executable_benchmark, canonical_dependency_metadata
def correlation_cluster(market):
 text=str(getattr(market,'question','')).upper()
 for asset in ('BITCOIN','BTC','ETHEREUM','ETH','SOLANA','SOL','XRP','DOGE','BNB'):
  if re.search(rf'\b{asset}\b',text):return asset
 return f'{getattr(market,"market_type","unknown")}:{getattr(market,"regime","baseline")}'
class PortfolioRisk:
 def __init__(self,memory):self.memory=memory
 def heat(self):return self.memory.hot().portfolio_heat
 def _exposure(self,d):
  if getattr(getattr(d,'mode',None),'value',None)=='live':
   return max(0.0,float(getattr(d,'executed_notional',0) or 0)+float(getattr(d,'executed_fees',0) or 0))
  quantity=max(0.0,float(getattr(d,'size',0) or 0)*float(getattr(d,'paper_fill_fraction',1) or 0))
  context=getattr(d,'market_context',{}) or {}
  price=getattr(d,'paper_execution_price',None)
  if price is None: price=getattr(d,'executable_price',None)
  if price is None: price=getattr(d,'price',0)
  return exposure_notional(quantity,price,float(context.get('fee_rate',0) or 0),float(context.get('slippage_bps',0) or 0))
 def gate(self,m,requested_size,flow_imbalance=0,large_wallet_signal=0,side='YES'):
  reasons=[];multiplier=1.0
  if self.memory.hot().daily_pnl<=-0.1:reasons.append('daily_kill_switch')
  if self.memory.hot().weekly_pnl<=-0.2:reasons.append('weekly_kill_switch')
  if abs(flow_imbalance)>.7 or large_wallet_signal>.8:reasons.append('toxic_flow');multiplier*=.25
  if m.resolution_hours>720:reasons.append('long_horizon_discount');multiplier*=.5
  open_positions=[d for d in self.memory.decisions() if d.outcome=='pending' and (getattr(d,'size',0)>0 or getattr(d,'executed_size',0)>0)]
  market_exposure=sum(self._exposure(d) for d in open_positions if d.market_id==m.market_id)
  bucket_exposure=sum(self._exposure(d) for d in open_positions if d.market_type==m.market_type and d.regime==m.regime)
  cluster=correlation_cluster(m);cluster_exposure=sum(self._exposure(d) for d in open_positions if (d.market_context or {}).get('correlation_cluster')==cluster)
  market_cap=max(.01,float(os.getenv('MAX_MARKET_EXPOSURE','.05')));bucket_cap=max(market_cap,float(os.getenv('MAX_BUCKET_EXPOSURE','.10')));cluster_cap=max(bucket_cap,float(os.getenv('MAX_CORRELATED_EXPOSURE','.12')))
  benchmark=executable_benchmark(m,side)
  unit_cost=max(1e-9,float(benchmark['cost']))
  requested_capital=max(0,float(requested_size))*unit_cost
  if market_exposure>=market_cap:reasons.append('market_exposure_gate');requested_capital=0
  elif market_exposure+requested_capital>market_cap:reasons.append('market_exposure_cap');requested_capital=market_cap-market_exposure
  if bucket_exposure>=bucket_cap:reasons.append('correlated_bucket_gate');requested_capital=0
  elif bucket_exposure+requested_capital>bucket_cap:requested_capital=min(requested_capital,bucket_cap-bucket_exposure);reasons.append('correlated_bucket_cap')
  if cluster_exposure>=cluster_cap:reasons.append('correlation_cluster_gate');requested_capital=0
  elif cluster_exposure+requested_capital>cluster_cap:reasons.append('correlation_cluster_cap');requested_capital=min(requested_capital,cluster_cap-cluster_exposure)
  hard={'daily_kill_switch','weekly_kill_switch','market_exposure_gate','correlated_bucket_gate','correlation_cluster_gate'}
  return (0 if reasons and any(x in reasons for x in hard) else max(0,requested_capital/unit_cost*multiplier)),reasons

 def dependency_metadata(self,m,market_id=None):
  return canonical_dependency_metadata(m.question,m.market_type,market_id=market_id or getattr(m,'market_id',None),underlying_asset=(getattr(m,'market_context',{}) or {}).get('underlying_asset'),resolution_end=getattr(m,'market_end_time',None),source=getattr(m,'source',None))

 def risk_metrics(self,pnls,exposures=None):
  from .research_validation import risk_metrics
  return risk_metrics(pnls,exposures)
