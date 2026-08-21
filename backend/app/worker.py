import json,os,time,logging,signal,threading
from datetime import datetime,timedelta,timezone
from .ingestion import IngestionRunner
from .models import DecisionRequest,Mode
from .fast_probability import FastMarketProbability
from .market_policy import fast_markets_only,fast_max_resolution_hours,fast_market_allowed
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
def _fast_max_hours():
 return fast_max_resolution_hours()

def _failure_backoff_seconds(base_interval,failure_streak,max_backoff=900):
 return min(max(1,float(max_backoff)),max(1,float(base_interval))*(2**min(10,max(0,int(failure_streak)))))

def autonomous_paper_cycle(runner,memory,decide_fn,fast_model=None):
 enabled=os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true'
 if not enabled or memory.hot().mode!=Mode.PAPER:return {'enabled':enabled,'evaluated':0,'traded':0,'skipped':0,'reason':'disabled_or_not_paper'}
 limit=max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3')));cooldown=max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600')));type_cap=max(1,int(os.getenv('AUTO_PAPER_MAX_PER_TYPE_PER_TICK','1')));min_hours=max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05')));configured_max=max(min_hours,float(os.getenv('AUTO_PAPER_MAX_RESOLUTION_HOURS','24')));fast_only=fast_markets_only();fast_max=_fast_max_hours();max_hours=min(configured_max,fast_max) if fast_only else configured_max;prefer_fast=os.getenv('AUTO_PAPER_PREFER_FAST_MARKETS','true').lower()=='true'
 now=datetime.now(timezone.utc);recent={};pending_markets=set()
 for decision in memory.decisions():
  if decision.source.startswith('polymarket'):
   if decision.outcome=='pending' and decision.size>0 and decision.paper_fill_fraction>0:pending_markets.add(decision.market_id)
   try:
    created=datetime.fromisoformat(decision.created_at.replace('Z','+00:00'))
    if decision.market_id not in recent or created>recent[decision.market_id]:recent[decision.market_id]=created
   except ValueError:continue
 type_counts={};evaluated=traded=skipped=0;horizon_skipped=0;candidate_count=0;page_size=max(50,min(100,int(os.getenv('AUTO_PAPER_MARKET_PAGE_SIZE','100'))));pages=max(1,min(10,int(os.getenv('AUTO_PAPER_MARKET_PAGES','5'))));items=[]
 for page in range(pages):
  # Gamma's default ordering is dominated by long-dated markets. Request
  # nearest-expiry ordering so five-minute BTC/ETH and similar markets are
  # discovered before the bounded page scan is exhausted.
  batch=runner.data.markets(page_size,offset=page*page_size,order='endDate',ascending=True,closed=False,end_date_min=now.isoformat().replace('+00:00','Z'),end_date_max=(now+timedelta(hours=max_hours)).isoformat().replace('+00:00','Z'))
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
 telemetry.set('vesper_autonomous_paper_candidate_count',candidate_count);telemetry.set('vesper_autonomous_paper_horizon_skipped',horizon_skipped);telemetry.set('vesper_autonomous_paper_min_resolution_hours',min_hours);telemetry.set('vesper_autonomous_paper_max_resolution_hours',max_hours);telemetry.set('vesper_autonomous_paper_fast_only',int(fast_only));telemetry.set('vesper_autonomous_paper_fast_max_hours',fast_max)
 for hours,item in ranked:
  if evaluated>=limit:break
  market_id=str(item.get('id') or item.get('conditionId') or '');market_type=str(item.get('category') or 'unknown')
  selection_type=f'{market_type}:{_duration_bucket(hours)}'
  if not market_id or market_id in pending_markets or (market_id in recent and (now-recent[market_id]).total_seconds()<cooldown) or type_counts.get(selection_type,0)>=type_cap:skipped+=1;continue
  yes_token,no_token=runner.data.token_pair(item)
  if not yes_token or not no_token:skipped+=1;telemetry.inc('vesper_dual_book_unavailable_total',labels={'reason':'missing_token_pair'});continue
  try:market_input=runner.data.to_input(item,yes_book=runner.data.book(yes_token),no_book=runner.data.book(no_token))
  except Exception:skipped+=1;continue
  if market_input.quality_score<.95 or market_input.market_status!='active':skipped+=1;continue
  try:runner.store.save_verified_input(market_input)
  except Exception as exc:skipped+=1;telemetry.error('verified_input_persist');log.warning('autonomous paper skipped market=%s reason=verified_input_persist error=%s',market_id,exc);continue
  reference=_number(item.get('reference_rate') or item.get('referenceRate'))
  if reference is None:
   if fast_model is None:fast_model=FastMarketProbability()
   model=fast_model.estimate(item,market_input,memory)
  else:model=None
  if model is not None:
   telemetry.inc('vesper_fast_model_estimates_total',labels={'model_version':model['model_version'],'asset':model['asset']})
   market_input.reference_rate=model['probability'];market_input.raw_model_probability=model['raw_probability'];market_input.model_probability=model['probability'];market_input.model_version=model['model_version'];market_input.model_lower_bound=model['lower_bound'];market_input.model_upper_bound=model['upper_bound'];market_input.model_uncertainty=model['uncertainty'];market_input.model_calibration_samples=model['calibration_samples'];market_input.model_calibration_status=model['calibration_status'];market_input.regime=model['regime'];market_input.signals={'fast_model':model['probability']};telemetry.set('vesper_fast_model_uncertainty',model['uncertainty']);telemetry.inc('vesper_fast_model_regime_total',labels={'regime':model['regime']})
  elif reference is not None:market_input.reference_rate=max(0,min(1,reference))
  else:skipped+=1;telemetry.inc('vesper_fast_model_unavailable_total');log.info('autonomous paper skipped market=%s reason=fast_model_unavailable',market_id);continue
  request=DecisionRequest(market=market_input,strategy_id=os.getenv('AUTO_PAPER_STRATEGY','reference_class'),execute=True,evidence_complete=True)
  try:decision=decide_fn(request,None)
  except Exception as exc:skipped+=1;log.warning('autonomous paper evaluation failed market=%s error=%s',market_id,exc);continue
  evaluated+=1;type_counts[selection_type]=type_counts.get(selection_type,0)+1;recent[market_id]=now
  if decision.size>0:traded+=1;pending_markets.add(market_id)
  log.info('autonomous paper evaluation market=%s horizon_hours=%.3f bucket=%s action=%s size=%.6f edge=%.6f',market_id,hours,selection_type,decision.action,decision.size,decision.edge)
 telemetry.inc('vesper_autonomous_paper_evaluations_total',value=evaluated);telemetry.inc('vesper_autonomous_paper_trades_total',value=traded);telemetry.set('vesper_autonomous_paper_enabled',1)
 return {'enabled':True,'evaluated':evaluated,'traded':traded,'skipped':skipped,'horizon_skipped':horizon_skipped,'candidates':candidate_count,'min_resolution_hours':min_hours,'max_resolution_hours':max_hours,'fast_only':fast_only,'fast_max_hours':fast_max}

def run():
 runner=IngestionRunner();fast_model=FastMarketProbability();interval=max(1,int(os.getenv('PIPELINE_INTERVAL_SECONDS','60')));max_backoff=max(interval,float(os.getenv('PIPELINE_MAX_BACKOFF_SECONDS','900')));failure_streak=0;stop=threading.Event();from .main import decide,memory,markets as api_markets
 def request_stop(signum,frame):
  log.info('pipeline shutdown requested signal=%s',signum);stop.set()
 for signal_name in (signal.SIGINT,signal.SIGTERM):signal.signal(signal_name,request_stop)
 log.info('pipeline started interval=%ss max_backoff=%ss auto_paper=%s',interval,max_backoff,os.getenv('AUTO_PAPER_ENABLED','true'))
 try:
  while not stop.is_set():
   try:
    result=runner.tick(max(1,int(os.getenv('INGEST_MARKET_LIMIT','50'))));auto=autonomous_paper_cycle(runner,memory,decide,fast_model);failure_streak=0;log.info('ingestion tick %s autonomous_paper=%s',result,auto)
   except Exception as exc:
    failure_streak=min(10,failure_streak+1)
    try:runner.store.record_heartbeat(error=exc)
    except Exception:log.exception('unable to persist worker error heartbeat')
    delay=_failure_backoff_seconds(interval,failure_streak,max_backoff);log.exception('ingestion tick failed; retrying in %.1fs: %s',delay,exc)
    if stop.wait(delay):break
    continue
   stop.wait(interval)
 finally:
  runner.data.close();api_markets.close();fast_model.close();log.info('pipeline stopped')
if __name__=='__main__':run()
