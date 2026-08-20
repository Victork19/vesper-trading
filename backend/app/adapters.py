from abc import ABC, abstractmethod
from .models import DecisionRecord, Mode, OrderStatus
import uuid
from .config import settings
class ExecutionAdapter(ABC):
 @abstractmethod
 def execute(self,decision:DecisionRecord)->dict:...
class PaperExecution(ExecutionAdapter):
 def execute(self,decision):return {'status':OrderStatus.FILLED.value,'capital_at_risk':0,'decision_id':decision.id,'client_order_id':'paper_'+uuid.uuid4().hex,'filled_size':decision.size,'average_fill_price':decision.executable_price or decision.price}
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
