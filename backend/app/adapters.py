from abc import ABC, abstractmethod
from .models import DecisionRecord, Mode, OrderStatus
import uuid
from .config import settings
def paper_fill_profile(market,size):
 # Deterministic, replayable microstructure model. It never invents a fill
 # when the observed book cannot support the requested exposure.
 if size<=0:return 0.0,'zero_size'
 depth=sum(level.size for level in (market.book_asks or []))
 if not depth:return 1.0,'no_depth_reported_conservative_full_fill'
 fraction=max(0.0,min(1.0,depth/size))
 if market.quality_score<.95:fraction*=max(0.25,market.quality_score)
 return fraction,'top_of_book_depth_and_quality'
class ExecutionAdapter(ABC):
 @abstractmethod
 def execute(self,decision:DecisionRecord)->dict:...
class PaperExecution(ExecutionAdapter):
 def execute(self,decision):
  filled=decision.size*decision.paper_fill_fraction
  return {'status':OrderStatus.FILLED.value if filled>0 else OrderStatus.REJECTED.value,'capital_at_risk':0,'decision_id':decision.id,'client_order_id':'paper_'+uuid.uuid4().hex,'filled_size':filled,'average_fill_price':decision.executable_price or decision.price}
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
