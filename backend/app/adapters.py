from abc import ABC, abstractmethod
from .models import DecisionRecord, Mode, OrderStatus
import uuid
from .config import settings
def paper_execution_profile(market,size,side=None):
 # Deterministic, replayable microstructure model. Walk asks in price order,
 # compute the volume-weighted quote actually consumed, then apply configured
 # slippage and binary-contract fees. Missing depth fails closed.
 if size<=0:return {'fill_fraction':0.0,'filled_size':0.0,'average_quote_price':None,'execution_price':None,'reason':'zero_size'}
 if side=='YES':asks=getattr(market,'yes_book_asks',None) or []
 elif side=='NO':asks=getattr(market,'no_book_asks',None) or []
 else:asks=getattr(market,'book_asks',None) or []
 levels=sorted((level for level in (asks or []) if level.size>0),key=lambda level:level.price)
 if not levels:return {'fill_fraction':0.0,'filled_size':0.0,'average_quote_price':None,'execution_price':None,'reason':'no_depth_reported_no_fill'}
 remaining=float(size);filled=notional=0.0
 for level in levels:
  take=min(remaining,float(level.size));filled+=take;notional+=take*float(level.price);remaining-=take
  if remaining<=1e-12:break
 if filled<=0:return {'fill_fraction':0.0,'filled_size':0.0,'average_quote_price':None,'execution_price':None,'reason':'no_fillable_depth'}
 average=notional/filled
 quality_multiplier=max(.25,float(getattr(market,'quality_score',1.0))) if market.quality_score<.95 else 1.0
 filled*=quality_multiplier
 execution=min(1.0,average+float(getattr(market,'slippage_bps',0))/10000+float(getattr(market,'fee_rate',0))*(1-average))
 return {'fill_fraction':max(0.0,min(1.0,filled/size)),'filled_size':filled,'average_quote_price':average,'execution_price':execution,'reason':'depth_walk_vwap_quality_adjusted' if quality_multiplier<1 else 'depth_walk_vwap'}

def paper_fill_profile(market,size,side=None):
 profile=paper_execution_profile(market,size,side)
 return profile['fill_fraction'],profile['reason']
class ExecutionAdapter(ABC):
 @abstractmethod
 def execute(self,decision:DecisionRecord)->dict:...
class PaperExecution(ExecutionAdapter):
 def execute(self,decision):
  filled=decision.size*decision.paper_fill_fraction
  status=OrderStatus.REJECTED if filled<=0 else OrderStatus.PARTIALLY_FILLED if filled+1e-12<decision.size else OrderStatus.FILLED
  execution_price=decision.paper_execution_price if decision.paper_execution_price is not None else decision.executable_price if decision.executable_price is not None else decision.price
  return {'status':status.value,'capital_at_risk':0,'decision_id':decision.id,'client_order_id':'paper_'+uuid.uuid4().hex,'filled_size':filled,'average_fill_price':execution_price}
class ShadowExecution(ExecutionAdapter):
 def execute(self,decision):return {'status':OrderStatus.ACCEPTED.value,'capital_at_risk':0,'decision_id':decision.id,'client_order_id':'shadow_'+uuid.uuid4().hex,'filled_size':0,'average_fill_price':None}
class LiveExecution(ExecutionAdapter):
 def execute(self,decision):
  if not settings.live_enabled or settings.max_capital<=0 or settings.max_order_size<=0: raise RuntimeError('Live execution is disabled until capital, limits, and operator gates are configured.')
  if decision.size>settings.max_order_size: raise RuntimeError('Order exceeds live maximum.')
  try:
   from py_clob_client_v2 import ClobClient
   if not __import__('os').getenv('POLYMARKET_PRIVATE_KEY'): raise RuntimeError('Polymarket signer is not configured.')
   raise RuntimeError('Live order lifecycle is not enabled: authenticated submission and reconciliation are required.')
  except ImportError: raise RuntimeError('Install py-clob-client-v2 before enabling live execution.')
def adapter_for(mode):return PaperExecution() if mode==Mode.PAPER else ShadowExecution() if mode==Mode.SHADOW else LiveExecution()
