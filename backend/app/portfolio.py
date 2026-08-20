from .models import MarketInput
class ToxicFlowDetector:
 def inspect(self,m:MarketInput,flow_imbalance:float=0,large_wallet_signal:float=0):
  flags=[]
  if abs(flow_imbalance)>.7:flags.append('order_flow_imbalance')
  if large_wallet_signal>.8:flags.append('large_informed_flow')
  if m.volume_24h>0 and m.liquidity>0 and m.volume_24h/m.liquidity>20:flags.append('volume_liquidity_spike')
  return {'toxic':bool(flags),'flags':flags,'size_multiplier':.25 if flags else 1.0}
class BucketKiller:
 def __init__(self,memory):self.memory=memory
 def suspended(self,strategy_id,market_type,regime):
  return any(x.strategy_id==strategy_id and x.market_type==market_type and x.regime==regime and x.expectancy<0 and x.decisions>=10 for x in self.memory.snapshots())
