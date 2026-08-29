from abc import ABC, abstractmethod
import os
from .models import DecisionRecord, Mode, OrderStatus
import uuid
from .config import settings
from .live_execution import LiveExecutionService, VenueError
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
 queue_factor=max(.05,min(1.0,float(os.getenv('PAPER_QUEUE_FILL_FACTOR','.85'))));quality_multiplier*=queue_factor
 latency_slippage=max(0.0,float(os.getenv('PAPER_LATENCY_SLIPPAGE_BPS','5')))
 filled*=quality_multiplier
 execution=min(1.0,average+(float(getattr(market,'slippage_bps',0))+latency_slippage)/10000+float(getattr(market,'fee_rate',0))*(1-average))
 return {'fill_fraction':max(0.0,min(1.0,filled/size)),'filled_size':filled,'average_quote_price':average,'execution_price':execution,'reason':'depth_walk_vwap_queue_adjusted' if quality_multiplier<1 else 'depth_walk_vwap'}

def paper_fill_profile(market,size,side=None):
 profile=paper_execution_profile(market,size,side)
 return profile['fill_fraction'],profile['reason']
def validate_execution_result(result,decision):
 if not isinstance(result,dict) or not result.get('client_order_id'):raise RuntimeError('Execution adapter returned no client order identity.')
 status=OrderStatus(result.get('status'))
 filled=float(result.get('filled_size',0));average=result.get('average_fill_price')
 if filled<0 or filled>decision.size+1e-12:raise RuntimeError('Execution adapter returned an invalid fill quantity.')
 if average is not None and not 0<=float(average)<=1:raise RuntimeError('Execution adapter returned an invalid fill price.')
 if status in (OrderStatus.REJECTED,OrderStatus.FAILED) and filled>1e-12:raise RuntimeError('Rejected or failed order cannot report a fill.')
 if status==OrderStatus.FILLED and filled+1e-12<decision.size:raise RuntimeError('Filled order did not report the requested quantity.')
 if status==OrderStatus.PARTIALLY_FILLED and not 0<filled<decision.size:raise RuntimeError('Partial order status has an invalid quantity.')
 return {'status':status.value,'client_order_id':str(result['client_order_id']),'filled_size':filled,'filled_notional':float(result.get('filled_notional',filled*float(average or 0)) or 0),'filled_fees':float(result.get('filled_fees',0) or 0),'average_fill_price':float(average) if average is not None else None,'venue_order_id':result.get('venue_order_id'),'capital_at_risk':result.get('capital_at_risk',0),'decision_id':result.get('decision_id',decision.id),'fills':result.get('fills',[])}
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
 def __init__(self, service=None): self.service=service
 def execute(self,decision):
  if not settings.live_enabled or settings.max_capital<=0 or settings.max_order_size<=0: raise RuntimeError('Live execution is disabled until capital, limits, and operator gates are configured.')
  if decision.size>settings.max_order_size: raise RuntimeError('Order exceeds live maximum.')
  if self.service is None: raise RuntimeError('Live execution service is not configured.')
  try:
   return self.service.submit(decision)
  except VenueError as exc:
   if exc.uncertain:
    from .live_execution import deterministic_client_order_id
    return {'status':OrderStatus.RECONCILIATION_REQUIRED.value,'client_order_id':deterministic_client_order_id(decision),'filled_size':0,'average_fill_price':None,'venue_order_id':None,'error':str(exc),'decision_id':decision.id}
   raise RuntimeError(str(exc)) from exc
def adapter_for(mode, live_service=None):return PaperExecution() if mode==Mode.PAPER else ShadowExecution() if mode==Mode.SHADOW else LiveExecution(live_service)
