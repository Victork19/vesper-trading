import json,os,time,logging
from datetime import datetime,timezone
from .ingestion import IngestionRunner
from .models import DecisionRequest,Mode
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'));log=logging.getLogger('vesper.pipeline')
def _number(value,default=None):
 try:return float(value) if value not in (None,'') else default
 except (TypeError,ValueError):return default

def autonomous_paper_cycle(runner,memory,decide_fn):
 enabled=os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true'
 if not enabled or memory.hot().mode!=Mode.PAPER:return {'enabled':enabled,'evaluated':0,'traded':0,'skipped':0,'reason':'disabled_or_not_paper'}
 limit=max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3')));cooldown=max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600')));type_cap=max(1,int(os.getenv('AUTO_PAPER_MAX_PER_TYPE_PER_TICK','1')))
 now=datetime.now(timezone.utc);recent={}
 for decision in memory.decisions():
  if decision.source.startswith('polymarket'):
   try:recent[decision.market_id]=datetime.fromisoformat(decision.created_at.replace('Z','+00:00'))
   except ValueError:continue
 type_counts={};evaluated=traded=skipped=0;items=runner.data.markets(max(20,limit*10))
 for item in items:
  if evaluated>=limit:break
  market_id=str(item.get('id') or item.get('conditionId') or '');market_type=str(item.get('category') or 'unknown')
  if not market_id or (market_id in recent and (now-recent[market_id]).total_seconds()<cooldown) or type_counts.get(market_type,0)>=type_cap:skipped+=1;continue
  tokens=item.get('clobTokenIds') or item.get('clobTokenIDs') or []
  if isinstance(tokens,str):
   try:tokens=json.loads(tokens)
   except json.JSONDecodeError:tokens=[]
  if not isinstance(tokens,list) or not tokens:skipped+=1;continue
  try:market_input=runner.data.to_input(item,runner.data.book(str(tokens[0])))
  except Exception:skipped+=1;continue
  if market_input.quality_score<.95 or market_input.market_status!='active':skipped+=1;continue
  reference=_number(item.get('reference_rate') or item.get('referenceRate'))
  if reference is not None:market_input.reference_rate=max(0,min(1,reference))
  request=DecisionRequest(market=market_input,strategy_id=os.getenv('AUTO_PAPER_STRATEGY','reference_class'),execute=True,evidence_complete=True)
  try:decision=decide_fn(request,None)
  except Exception as exc:skipped+=1;log.warning('autonomous paper evaluation failed market=%s error=%s',market_id,exc);continue
  evaluated+=1;type_counts[market_type]=type_counts.get(market_type,0)+1;recent[market_id]=now
  if decision.size>0:traded+=1
  log.info('autonomous paper evaluation market=%s action=%s size=%.6f edge=%.6f',market_id,decision.action,decision.size,decision.edge)
 from .observability import telemetry
 telemetry.inc('vesper_autonomous_paper_evaluations_total',value=evaluated);telemetry.inc('vesper_autonomous_paper_trades_total',value=traded);telemetry.set('vesper_autonomous_paper_enabled',1)
 return {'enabled':True,'evaluated':evaluated,'traded':traded,'skipped':skipped}

def run():
 runner=IngestionRunner();interval=int(os.getenv('PIPELINE_INTERVAL_SECONDS','60'));from .main import decide,memory;log.info('pipeline started interval=%ss auto_paper=%s',interval,os.getenv('AUTO_PAPER_ENABLED','true'))
 while True:
  try:
   result=runner.tick(int(os.getenv('INGEST_MARKET_LIMIT','50')));auto=autonomous_paper_cycle(runner,memory,decide);log.info('ingestion tick %s autonomous_paper=%s',result,auto)
  except Exception as exc:runner.store.record_heartbeat(error=exc);log.exception('ingestion tick failed: %s',exc)
  time.sleep(interval)
if __name__=='__main__':run()
