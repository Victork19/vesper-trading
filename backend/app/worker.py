import json,os,time,logging
from datetime import datetime,timezone
from .ingestion import IngestionRunner
from .models import DecisionRequest,Mode
from .fast_probability import FastMarketProbability
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'));log=logging.getLogger('vesper.pipeline')
def _number(value,default=None):
 try:return float(value) if value not in (None,'') else default
 except (TypeError,ValueError):return default
def _end_time(item):
 value=item.get('endDate') or item.get('end_date') or item.get('endTime')
 if not value:return None
 try:return datetime.fromisoformat(str(value).replace('Z','+00:00'))
 except (TypeError,ValueError):return None
def _duration_bucket(hours):
 return '5m' if hours<=.25 else 'intraday' if hours<=24 else 'daily'

def autonomous_paper_cycle(runner,memory,decide_fn):
 enabled=os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true'
 if not enabled or memory.hot().mode!=Mode.PAPER:return {'enabled':enabled,'evaluated':0,'traded':0,'skipped':0,'reason':'disabled_or_not_paper'}
 limit=max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3')));cooldown=max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600')));type_cap=max(1,int(os.getenv('AUTO_PAPER_MAX_PER_TYPE_PER_TICK','1')));min_hours=max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05')));max_hours=max(min_hours,float(os.getenv('AUTO_PAPER_MAX_RESOLUTION_HOURS','24')));prefer_fast=os.getenv('AUTO_PAPER_PREFER_FAST_MARKETS','true').lower()=='true'
 now=datetime.now(timezone.utc);recent={};pending_markets=set();fast_model=FastMarketProbability()
 for decision in memory.decisions():
  if decision.source.startswith('polymarket'):
   if decision.outcome=='pending' and decision.size>0:pending_markets.add(decision.market_id)
   try:recent[decision.market_id]=datetime.fromisoformat(decision.created_at.replace('Z','+00:00'))
   except ValueError:continue
 type_counts={};evaluated=traded=skipped=0;horizon_skipped=0;candidate_count=0;page_size=max(50,min(100,int(os.getenv('AUTO_PAPER_MARKET_PAGE_SIZE','100'))));pages=max(1,min(10,int(os.getenv('AUTO_PAPER_MARKET_PAGES','5'))));items=[]
 for page in range(pages):
  batch=runner.data.markets(page_size,offset=page*page_size)
  if not batch:break
  items.extend(batch)
  if len(batch)<page_size:break
 items={str(item.get('id') or item.get('conditionId')):item for item in items}.values()
 items=list(items);ranked=[]
 for item in items:
  end_time=_end_time(item)
  if end_time is None:
   horizon_skipped+=1;continue
  hours=(end_time-now).total_seconds()/3600
  if hours<min_hours or hours>max_hours:
   horizon_skipped+=1;continue
  ranked.append((hours,item))
 candidate_count=len(ranked);ranked.sort(key=lambda pair:pair[0],reverse=not prefer_fast)
 from .observability import telemetry
 telemetry.set('vesper_autonomous_paper_candidate_count',candidate_count);telemetry.set('vesper_autonomous_paper_horizon_skipped',horizon_skipped);telemetry.set('vesper_autonomous_paper_min_resolution_hours',min_hours);telemetry.set('vesper_autonomous_paper_max_resolution_hours',max_hours)
 for hours,item in ranked:
  if evaluated>=limit:break
  market_id=str(item.get('id') or item.get('conditionId') or '');market_type=str(item.get('category') or 'unknown')
  selection_type=f'{market_type}:{_duration_bucket(hours)}'
  if not market_id or market_id in pending_markets or (market_id in recent and (now-recent[market_id]).total_seconds()<cooldown) or type_counts.get(selection_type,0)>=type_cap:skipped+=1;continue
  tokens=item.get('clobTokenIds') or item.get('clobTokenIDs') or []
  if isinstance(tokens,str):
   try:tokens=json.loads(tokens)
   except json.JSONDecodeError:tokens=[]
  if not isinstance(tokens,list) or not tokens:skipped+=1;continue
  try:market_input=runner.data.to_input(item,runner.data.book(str(tokens[0])))
  except Exception:skipped+=1;continue
  if market_input.quality_score<.95 or market_input.market_status!='active':skipped+=1;continue
  reference=_number(item.get('reference_rate') or item.get('referenceRate'))
  model=fast_model.estimate(item,market_input,memory) if reference is None else None
  if model is not None:
   telemetry.inc('vesper_fast_model_estimates_total',labels={'model_version':model['model_version'],'asset':model['asset']})
   market_input.reference_rate=model['probability'];market_input.raw_model_probability=model['raw_probability'];market_input.model_version=model['model_version'];market_input.signals={'fast_model':model['probability']}
  elif reference is not None:market_input.reference_rate=max(0,min(1,reference))
  else:skipped+=1;telemetry.inc('vesper_fast_model_unavailable_total');log.info('autonomous paper skipped market=%s reason=fast_model_unavailable',market_id);continue
  request=DecisionRequest(market=market_input,strategy_id=os.getenv('AUTO_PAPER_STRATEGY','reference_class'),execute=True,evidence_complete=True)
  try:decision=decide_fn(request,None)
  except Exception as exc:skipped+=1;log.warning('autonomous paper evaluation failed market=%s error=%s',market_id,exc);continue
  evaluated+=1;type_counts[selection_type]=type_counts.get(selection_type,0)+1;recent[market_id]=now
  if decision.size>0:traded+=1;pending_markets.add(market_id)
  log.info('autonomous paper evaluation market=%s horizon_hours=%.3f bucket=%s action=%s size=%.6f edge=%.6f',market_id,hours,selection_type,decision.action,decision.size,decision.edge)
 telemetry.inc('vesper_autonomous_paper_evaluations_total',value=evaluated);telemetry.inc('vesper_autonomous_paper_trades_total',value=traded);telemetry.set('vesper_autonomous_paper_enabled',1)
 return {'enabled':True,'evaluated':evaluated,'traded':traded,'skipped':skipped,'horizon_skipped':horizon_skipped,'candidates':candidate_count,'min_resolution_hours':min_hours,'max_resolution_hours':max_hours}

def run():
 runner=IngestionRunner();interval=int(os.getenv('PIPELINE_INTERVAL_SECONDS','60'));from .main import decide,memory;log.info('pipeline started interval=%ss auto_paper=%s',interval,os.getenv('AUTO_PAPER_ENABLED','true'))
 while True:
  try:
   result=runner.tick(int(os.getenv('INGEST_MARKET_LIMIT','50')));auto=autonomous_paper_cycle(runner,memory,decide);log.info('ingestion tick %s autonomous_paper=%s',result,auto)
  except Exception as exc:runner.store.record_heartbeat(error=exc);log.exception('ingestion tick failed: %s',exc)
  time.sleep(interval)
if __name__=='__main__':run()
