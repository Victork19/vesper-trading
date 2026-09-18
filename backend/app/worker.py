import hashlib,json,os,time,logging,signal,threading,uuid,sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime,timedelta,timezone
from .ingestion import IngestionRunner
from .models import DecisionRequest,Mode,OrderStatus,OrderRecord
from .fast_probability import FastMarketProbability,market_asset
from .market_policy import fast_markets_only,fast_max_resolution_hours,fast_market_allowed
from .observability import telemetry
from .ensemble import hybrid_forecast
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

def _generic_market_baseline(memory,market_input):
 cutoff=market_input.observed_at or datetime.now(timezone.utc);outcomes=[]
 for d in memory.decisions():
  if d.market_type!=market_input.market_type or d.resolved_yes is None or d.outcome in ('pending','void') or not d.research_eligible:continue
  try:
   created=datetime.fromisoformat(str(d.created_at).replace('Z','+00:00'));resolved=datetime.fromisoformat(str(d.resolved_at).replace('Z','+00:00')) if d.resolved_at else None
   if resolved is None or created>=cutoff or resolved>cutoff:continue
  except (TypeError,ValueError):continue
  outcomes.append(1 if d.resolved_yes else 0)
 samples=len(outcomes);empirical=sum(outcomes)/samples if samples else market_input.price;weight=min(.55,samples/(samples+60));probability=max(.01,min(.99,market_input.price*(1-weight)+empirical*weight));uncertainty=max(.12,min(.49,.45/(max(1,samples)**.5)))
 return {'model_version':'market_baseline_v2','raw_probability':market_input.price,'probability':probability,'lower_bound':max(.01,probability-uncertainty),'upper_bound':min(.99,probability+uncertainty),'uncertainty':uncertainty,'calibration_samples':samples,'calibration_status':'usable' if samples>=20 else 'warming','regime':'baseline','asset':'market','direction':'market'}

def _apply_forecast(market_input,forecast):
 market_input.reference_rate=forecast['probability'];market_input.raw_model_probability=forecast.get('raw_probability',forecast['probability']);market_input.model_probability=forecast['probability'];market_input.model_version=forecast['model_version'];market_input.model_provenance={'provider':'vesper_strategy_tournament','version':forecast['model_version'],'components':forecast.get('components',[]),'weighting':forecast.get('weighting',{}),'calibration_samples':forecast.get('calibration_samples',0),'observed_at':market_input.observed_at.isoformat() if market_input.observed_at else None};market_input.model_lower_bound=forecast['lower_bound'];market_input.model_upper_bound=forecast['upper_bound'];market_input.model_uncertainty=forecast['uncertainty'];market_input.model_calibration_samples=forecast.get('calibration_samples',0);market_input.model_calibration_status=forecast.get('calibration_status','warming');market_input.signals={}

def _strategy_arms(fast_available):
 if os.getenv('AUTO_PAPER_MULTI_STRATEGY_ENABLED','true').lower()!='true':return [os.getenv('AUTO_PAPER_STRATEGY','reference_class')]
 configured=[item.strip() for item in os.getenv('AUTO_PAPER_STRATEGY_ARMS','reference_class,fast_model,hybrid_ensemble').split(',') if item.strip()]
 allowed={'reference_class'}
 if fast_available:allowed.update({'fast_model','hybrid_ensemble'})
 arms=[item for item in configured if item in allowed]
 return arms or ['reference_class']

def _select_strategy(memory,market_id,arms):
 counts={arm:0 for arm in arms}
 for decision in memory.decisions():
  if decision.strategy_id in counts:counts[decision.strategy_id]+=1
 minimum=min(counts.values())
 candidates=[arm for arm in arms if counts[arm]==minimum]
 digest=hashlib.sha256(str(market_id).encode()).hexdigest()
 return candidates[int(digest[:8],16)%len(candidates)],counts

def _failure_backoff_seconds(base_interval,failure_streak,max_backoff=900):
 return min(max(1,float(max_backoff)),max(1,float(base_interval))*(2**min(10,max(0,int(failure_streak)))))

_TERMINAL_ORDER_STATES={'filled','canceled','expired','rejected','failed'}

def _lease_owner(): return f'worker_{os.getpid()}_{uuid.uuid4().hex[:10]}'

def acquire_reconciliation_lease(memory, order_id, owner, ttl_seconds=None):
 """Claim one order for one reconciler, atomically and crash-safely."""
 ttl=max(1,float(ttl_seconds or os.getenv('RECONCILIATION_LEASE_SECONDS','30')))
 with memory.db.connection() as c:
  row=c.execute("""INSERT INTO reconciliation_leases(order_id,lease_owner,lease_expires_at,started_at,attempt_count,updated_at)
      VALUES(%s,%s,NOW()+(%s * INTERVAL '1 second'),NOW(),1,NOW())
      ON CONFLICT(order_id) DO UPDATE SET lease_owner=EXCLUDED.lease_owner,
        lease_expires_at=EXCLUDED.lease_expires_at,started_at=NOW(),
        attempt_count=reconciliation_leases.attempt_count+1,updated_at=NOW()
      WHERE reconciliation_leases.lease_expires_at<NOW() OR reconciliation_leases.lease_owner=%s
      RETURNING order_id""",(order_id,owner,ttl,owner)).fetchone()
 return bool(row)

def finish_reconciliation_lease(memory, order_id, owner, success=True, error=None, status=None):
 with memory.db.connection() as c:
  c.execute("""UPDATE reconciliation_leases SET lease_expires_at=NOW(),last_success_at=CASE WHEN %s THEN NOW() ELSE last_success_at END,
      consecutive_failures=CASE WHEN %s THEN 0 ELSE consecutive_failures+1 END,last_error=%s,last_order_status=%s,updated_at=NOW()
      WHERE order_id=%s AND lease_owner=%s""",(success,success,error,status,order_id,owner))

def _deadline_call(fn, *args, timeout=None):
 """Bound a venue operation so a stuck SDK call cannot wedge reconciliation."""
 seconds=max(.1,float(timeout or os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15')))
 executor=ThreadPoolExecutor(max_workers=1)
 future=executor.submit(fn,*args)
 try:return future.result(timeout=seconds)
 except FutureTimeout as exc:
  future.cancel();raise TimeoutError(f'venue operation exceeded {seconds:.1f}s deadline') from exc
 finally:
  executor.shutdown(wait=False,cancel_futures=True)

def _atomic_apply_reconciliation(memory, order, result):
 """Persist order, decision projection, and exposure delta under one lock.

 The venue result is never applied using the caller's stale `order` object.
 The row is re-read under the portfolio advisory lock, so concurrent
 reconciliation cannot double-count heat or regress a fill.
 """
 status=result.get('status')
 if status not in {x.value for x in OrderStatus}:raise ValueError(f'unknown reconciliation status: {status}')
 new_filled=float(result.get('filled_size',0) or 0);new_notional=float(result.get('filled_notional',0) or 0);new_fees=float(result.get('filled_fees',0) or 0);new_price=result.get('average_fill_price')
 with memory.db.connection() as c:
  c.execute("SELECT pg_advisory_xact_lock(hashtext('vesper-portfolio-exposure'))")
  current=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(order.id,)).fetchone()
  if not current:return False
  old_filled=float(current['filled_size'] or 0);old_notional=float(current.get('filled_notional',0) or 0);old_fees=float(current.get('filled_fees',0) or 0)
  if new_filled+1e-12<old_filled:raise ValueError('reconciliation fill quantity regressed')
  if new_notional+1e-9<old_notional or new_fees+1e-9<old_fees:raise ValueError('reconciliation fill economics regressed')
  if new_filled>float(current['requested_size'])+1e-12:raise ValueError('reconciliation fill exceeds requested size')
  if new_filled>1e-12 and new_notional<0:raise ValueError('reconciliation notional cannot be negative')
  if new_filled>1e-12 and new_price is not None and abs(float(new_price)-new_notional/new_filled)>1e-8:raise ValueError('average fill price is not weighted from immutable fills')
  if current['status'] in _TERMINAL_ORDER_STATES and (abs(new_filled-old_filled)>1e-12 or abs(new_notional-old_notional)>1e-9 or abs(new_fees-old_fees)>1e-9):raise ValueError('terminal order fill state is immutable')
  c.execute("""UPDATE orders SET status=%s,filled_size=%s,filled_notional=%s,filled_fees=%s,average_fill_price=%s,error=NULL,updated_at=NOW() WHERE id=%s""",(status,new_filled,new_notional,new_fees,new_price,order.id))
  if status in _TERMINAL_ORDER_STATES:
   c.execute("UPDATE execution_attempts SET status=%s WHERE client_order_id=%s AND action='intent' AND status NOT IN ('failed','rejected','canceled')",(status,order.client_order_id))
  delta=(new_notional+new_fees)-(old_notional+old_fees)
  if abs(delta)>1e-12:
   hotrow=c.execute("SELECT value FROM memory WHERE tier='HOT' AND key='state' FOR UPDATE").fetchone()
   if hotrow:
    hot=json.loads(hotrow['value']) if isinstance(hotrow['value'],str) else dict(hotrow['value'])
    hot['portfolio_heat']=max(0.0,float(hot.get('portfolio_heat',0))+delta)
    hot['open_risk']=max(0.0,float(hot.get('open_risk',0))+delta)
    c.execute("UPDATE memory SET value=%s,updated_at=NOW() WHERE tier='HOT' AND key='state'",(memory.db.json(hot),))
  decision_row=c.execute("SELECT value FROM memory WHERE tier='COLD' AND key=%s FOR UPDATE",(order.decision_id,)).fetchone()
  if decision_row:
   data=json.loads(decision_row['value']) if isinstance(decision_row['value'],str) else dict(decision_row['value'])
   data.update(executed_size=new_filled,executed_notional=new_notional,executed_fees=new_fees,executed_average_price=new_price,execution_reconciled=status in _TERMINAL_ORDER_STATES)
   c.execute("UPDATE memory SET value=%s,updated_at=NOW() WHERE tier='COLD' AND key=%s",(memory.db.json(data),order.decision_id))
 return True

def reconcile_live_orders(memory, service, owner=None):
 """Reconcile every non-terminal live order and release unfilled reserves."""
 checked=updated=errors=0
 owner=owner or _lease_owner()
 terminal={x.value for x in OrderStatus if x.value in ('filled','canceled','expired','rejected','failed')}
 for order in memory.orders():
  if order.mode.value!='live' or order.status.value in terminal or not order.venue_order_id: continue
  if not acquire_reconciliation_lease(memory,order.id,owner):continue
  checked+=1
  try:
   result=_deadline_call(service.reconcile,order,timeout=os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15'));status=result.get('status')
   if status in {x.value for x in OrderStatus}:
    if _atomic_apply_reconciliation(memory,order,result):updated+=1
    finish_reconciliation_lease(memory,order.id,owner,True,status=status)
   else:errors+=1;finish_reconciliation_lease(memory,order.id,owner,False,'invalid reconciliation result',status=status)
  except Exception as exc:
   errors+=1;finish_reconciliation_lease(memory,order.id,owner,False,str(exc),status='reconciliation_required');log.warning('order reconciliation failed order=%s: %s',order.id,exc)
 telemetry.set('vesper_live_orders_checked',checked);telemetry.set('vesper_live_orders_reconciled',updated);telemetry.set('vesper_live_orders_reconciliation_errors',errors)
 return {'checked':checked,'updated':updated,'errors':errors}

def recover_submission_intents(memory, service=None):
 """Promote venue acknowledgements persisted before an API crash into orders."""
 recovered=0
 with memory.db.connection() as c:
  rows=c.execute("""SELECT i.decision_id,i.client_order_id,i.request,i.response,i.status
      FROM execution_attempts i LEFT JOIN orders o ON o.client_order_id=i.client_order_id
      WHERE i.action='intent' AND i.status IN ('pending','uncertain','accepted','filled','partially_filled','reconciliation_required') AND o.id IS NULL""").fetchall()
 for row in rows:
  request=row['request'] or {};response=row['response'] or {};status=response.get('status',row['status'])
  if service and row['status'] in ('pending','uncertain'):
   recovered_result=service.recover_submission_intent(row['client_order_id'])
   if recovered_result and recovered_result.get('venue_order_id'):
    response=recovered_result.get('response',recovered_result);status=recovered_result.get('status','accepted')
    with memory.db.connection() as c:c.execute("UPDATE execution_attempts SET status=%s,response=%s,error=NULL WHERE decision_id=%s AND client_order_id=%s AND attempt=-1 AND action='intent'",(status,memory.db.json(response),row['decision_id'],row['client_order_id']))
   else: continue
  if status not in {x.value for x in OrderStatus}: status='accepted'
  try:
   order=OrderRecord(id='recovered_'+str(row['client_order_id'])[-32:],client_order_id=row['client_order_id'],decision_id=row['decision_id'],mode=Mode.LIVE,market_id=str(request.get('market_id','unknown')),side=str(request.get('side','UNKNOWN')),requested_size=float(request.get('size',0)),limit_price=float(request.get('price',0)),status=OrderStatus(status),filled_size=float(response.get('filled_size',0) or 0),filled_notional=float(response.get('filled_notional',0) or 0),filled_fees=float(response.get('filled_fees',0) or 0),average_fill_price=response.get('average_fill_price'),venue_order_id=response.get('venue_order_id') or response.get('id'))
   memory.save_order(order)
   if service:
    if isinstance(response.get('fills'),list) and response.get('fills'):
     service._ingest_fills(order,response)
     totals=service._fill_totals(order)
     order.filled_size=float(totals['quantity'] or 0)
     order.filled_notional=float(totals['notional'] or 0)
     order.filled_fees=float(totals['fees'] or 0)
     order.average_fill_price=order.filled_notional/order.filled_size if order.filled_size else None
     memory.save_order(order)
    reservation_state='partially_filled' if order.status==OrderStatus.PARTIALLY_FILLED else 'submitted' if order.status in (OrderStatus.ACCEPTED,OrderStatus.UNKNOWN) else order.status.value
    service.update_reservation(order.client_order_id,reservation_state,order.filled_notional+order.filled_fees,order.id)
   decision=next((item for item in memory.decisions() if item.id==order.decision_id),None)
   if decision:
    decision.executed_size=order.filled_size;decision.executed_notional=order.filled_notional;decision.executed_fees=order.filled_fees;decision.executed_average_price=order.average_fill_price;decision.execution_reconciled=order.status in _TERMINAL_ORDER_STATES
    memory.save_decision(decision)
   recovered+=1
  except Exception: continue
 telemetry.set('vesper_recovered_submission_intents',recovered);return recovered

def autonomous_paper_cycle(runner,memory,decide_fn,fast_model=None):
 enabled=os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true'
 if not enabled or memory.hot().mode!=Mode.PAPER:return {'enabled':enabled,'evaluated':0,'traded':0,'skipped':0,'reason':'disabled_or_not_paper'}
 limit=max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3')));cooldown=max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600')));type_cap=max(1,int(os.getenv('AUTO_PAPER_MAX_PER_TYPE_PER_TICK','1')));exploration_enabled=os.getenv('AUTO_PAPER_EXPLORATION_ENABLED','true').lower()=='true';exploration_limit=max(0,int(os.getenv('AUTO_PAPER_EXPLORATION_MAX_PER_TICK','2')));min_hours=max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05')));configured_max=max(min_hours,float(os.getenv('AUTO_PAPER_MAX_RESOLUTION_HOURS','24')));fast_only=fast_markets_only();fast_max=_fast_max_hours();max_hours=min(configured_max,fast_max) if fast_only else configured_max;prefer_fast=os.getenv('AUTO_PAPER_PREFER_FAST_MARKETS','true').lower()=='true'
 now=datetime.now(timezone.utc);recent={};pending_markets=set()
 for decision in memory.decisions():
  if decision.source.startswith('polymarket'):
   if decision.outcome=='pending' and decision.size>0 and decision.paper_fill_fraction>0:pending_markets.add(decision.market_id)
   try:
    created=datetime.fromisoformat(decision.created_at.replace('Z','+00:00'))
    if decision.market_id not in recent or created>recent[decision.market_id]:recent[decision.market_id]=created
   except ValueError:continue
 type_counts={};evaluated=traded=exploration_traded=skipped=0;horizon_skipped=0;candidate_count=0;page_size=max(50,min(100,int(os.getenv('AUTO_PAPER_MARKET_PAGE_SIZE','100'))));pages=max(1,min(10,int(os.getenv('AUTO_PAPER_MARKET_PAGES','5'))));items=[]
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
  # Model-backed crypto markets get first look, while generic fast markets
  # remain in the candidate pool for baseline learning and future models.
  ranked.append((0 if market_asset(item.get('question')) else 1,hours,item))
 candidate_count=len(ranked);ranked.sort(key=lambda pair:pair[0],reverse=not prefer_fast)
 telemetry.set('vesper_autonomous_paper_candidate_count',candidate_count);telemetry.set('vesper_autonomous_paper_horizon_skipped',horizon_skipped);telemetry.set('vesper_autonomous_paper_min_resolution_hours',min_hours);telemetry.set('vesper_autonomous_paper_max_resolution_hours',max_hours);telemetry.set('vesper_autonomous_paper_fast_only',int(fast_only));telemetry.set('vesper_autonomous_paper_fast_max_hours',fast_max)
 ranked.sort(key=lambda pair:(pair[0],pair[1] if prefer_fast else -pair[1]))
 # Do not let a large Gamma result set starve the next ingestion/resolution
 # cycle. Local eligibility checks happen before the bounded network scan, so
 # unsupported, pending, and cooling-down markets cannot starve valid ones.
 scan_limit=max(limit,min(100,int(os.getenv('AUTO_PAPER_CANDIDATE_SCAN_LIMIT','20'))))
 telemetry.set('vesper_autonomous_paper_scan_limit',scan_limit)
 if candidate_count>scan_limit:
  log.info('autonomous paper candidate scan bounded candidates=%s eligible_scan_limit=%s',candidate_count,scan_limit)
 eligible_ranked=[]
 unsupported_count=0
 for priority,hours,item in ranked:
  market_id=str(item.get('id') or item.get('conditionId') or '')
  if not market_id:
   skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':'missing_market_id'});log.info('autonomous paper skipped market=unknown reason=missing_market_id');continue
  if market_id in pending_markets:
   skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':'pending_market'});log.info('autonomous paper skipped market=%s reason=pending_market',market_id);continue
  if market_id in recent and (now-recent[market_id]).total_seconds()<cooldown:
   skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':'cooldown'});log.info('autonomous paper skipped market=%s reason=cooldown',market_id);continue
  yes_token,no_token=runner.data.token_pair(item)
  if not yes_token or not no_token:
   skipped+=1
   unsupported_count+=1
   continue
  eligible_ranked.append((priority,hours,item,yes_token,no_token))
  if len(eligible_ranked)>=scan_limit:
   break
 if unsupported_count:
  telemetry.inc('vesper_dual_book_unavailable_total',value=unsupported_count,labels={'reason':'missing_token_pair'})
  log.info('autonomous paper unsupported candidates skipped=%s eligible_scan=%s',unsupported_count,len(eligible_ranked))
 for priority,hours,item,yes_token,no_token in eligible_ranked:
  if evaluated>=limit:break
  market_id=str(item.get('id') or item.get('conditionId') or '');market_type=str(item.get('category') or 'unknown')
  selection_type=f'{market_type}:{_duration_bucket(hours)}'
  if not market_id or market_id in pending_markets or (market_id in recent and (now-recent[market_id]).total_seconds()<cooldown) or type_counts.get(selection_type,0)>=type_cap:
   reason='missing_market_id' if not market_id else 'pending_market' if market_id in pending_markets else 'cooldown' if market_id in recent and (now-recent[market_id]).total_seconds()<cooldown else 'type_cap'
   skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':reason});log.info('autonomous paper skipped market=%s reason=%s',market_id or 'unknown',reason);continue
  try:market_input=runner.data.to_input(item,yes_book=runner.data.book(yes_token),no_book=runner.data.book(no_token))
  except Exception as exc:skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':'market_input_error'});log.warning('autonomous paper skipped market=%s reason=market_input_error error=%s',market_id,exc);continue
  if market_input.quality_score<.95 or market_input.market_status!='active':
   reason='market_quality' if market_input.quality_score<.95 else 'market_not_active'
   skipped+=1;telemetry.inc('vesper_autonomous_paper_skips_total',labels={'reason':reason});log.info('autonomous paper skipped market=%s reason=%s quality=%.3f status=%s',market_id,reason,market_input.quality_score,market_input.market_status);continue
  try:runner.store.save_verified_input(market_input)
  except Exception as exc:skipped+=1;telemetry.error('verified_input_persist');log.warning('autonomous paper skipped market=%s reason=verified_input_persist error=%s',market_id,exc);continue
  reference=_number(item.get('reference_rate') or item.get('referenceRate'));baseline=_generic_market_baseline(memory,market_input)
  if reference is not None:
   baseline['probability']=max(.01,min(.99,reference));baseline['raw_probability']=baseline['probability'];baseline['uncertainty']=max(.12,min(.3,baseline['uncertainty']));baseline['lower_bound']=max(.01,baseline['probability']-baseline['uncertainty']);baseline['upper_bound']=min(.99,baseline['probability']+baseline['uncertainty']);baseline['model_version']='reference_class_v1';baseline['calibration_status']='reference_rate'
  if fast_model is None:fast_model=FastMarketProbability()
  model=fast_model.estimate(item,market_input,memory)
  if model is not None:
   telemetry.inc('vesper_fast_model_estimates_total',labels={'model_version':model['model_version'],'asset':model['asset']});telemetry.set('vesper_fast_model_uncertainty',model['uncertainty']);telemetry.inc('vesper_fast_model_regime_total',labels={'regime':model['regime']})
  arms=_strategy_arms(model is not None);strategy_id,arm_counts=_select_strategy(memory,market_id,arms);forecast=baseline
  if strategy_id=='fast_model' and model is not None:forecast=model
  elif strategy_id=='hybrid_ensemble' and model is not None:forecast=hybrid_forecast(baseline,model,memory);telemetry.set('vesper_ensemble_fast_weight',forecast['components'][1]['weight'])
  _apply_forecast(market_input,forecast);market_input.regime=model['regime'] if strategy_id=='fast_model' and model is not None else 'baseline'
  telemetry.inc('vesper_strategy_arm_selected_total',labels={'strategy':strategy_id});telemetry.set('vesper_strategy_arm_count_'+strategy_id,arm_counts.get(strategy_id,0))
  if strategy_id=='reference_class':telemetry.inc('vesper_generic_market_baseline_total');log.info('autonomous paper using market baseline market=%s',market_id)
  elif strategy_id=='hybrid_ensemble':log.info('autonomous paper using hybrid ensemble market=%s components=baseline+fast_model',market_id)
  request=DecisionRequest(market=market_input,strategy_id=strategy_id,execute=True,evidence_complete=True)
  try:decision=decide_fn(request,None)
  except Exception as exc:skipped+=1;log.warning('autonomous paper evaluation failed market=%s error=%s',market_id,exc);continue
  evaluated+=1;type_counts[selection_type]=type_counts.get(selection_type,0)+1;recent[market_id]=now
  if decision.size>0:traded+=1;pending_markets.add(market_id)
  elif exploration_enabled and exploration_traded<exploration_limit:
   exploration_market=market_input.model_copy(deep=True);underlying_model=exploration_market.model_version or 'market_baseline_v2';exploration_market.model_version='paper_exploration_v1';exploration_market.model_provenance={'provider':'paper_exploration','version':'paper_exploration_v1','selection':'lowest executable ask','underlying_model':underlying_model,'observed_at':exploration_market.observed_at.isoformat() if exploration_market.observed_at else None}
   exploration_request=DecisionRequest(market=exploration_market,strategy_id='paper_exploration',execute=True,evidence_complete=True,exploration=True)
   try:exploration_decision=decide_fn(exploration_request,None)
   except Exception as exc:log.warning('paper exploration failed market=%s error=%s',market_id,exc);exploration_decision=None
   if exploration_decision is not None and exploration_decision.size>0:
    decision=exploration_decision;traded+=1;exploration_traded+=1;pending_markets.add(market_id);log.info('paper exploration trade market=%s side=%s size=%.6f edge=%.6f excluded_from_research=true',market_id,decision.side,decision.size,decision.edge)
  log.info('autonomous paper evaluation market=%s horizon_hours=%.3f bucket=%s action=%s size=%.6f edge=%.6f strategy=%s',market_id,hours,selection_type,decision.action,decision.size,decision.edge,decision.strategy_id)
 telemetry.inc('vesper_autonomous_paper_evaluations_total',value=evaluated);telemetry.inc('vesper_autonomous_paper_trades_total',value=traded);telemetry.inc('vesper_autonomous_paper_exploration_trades_total',value=exploration_traded);telemetry.set('vesper_autonomous_paper_enabled',1);telemetry.set('vesper_autonomous_paper_exploration_enabled',int(exploration_enabled))
 return {'enabled':True,'evaluated':evaluated,'traded':traded,'exploration_traded':exploration_traded,'skipped':skipped,'horizon_skipped':horizon_skipped,'candidates':candidate_count,'min_resolution_hours':min_hours,'max_resolution_hours':max_hours,'fast_only':fast_only,'fast_max_hours':fast_max}

def run():
 if '--healthcheck' in sys.argv:
  from .ingestion import IngestionStore
  if IngestionStore().worker_health().get('stale',True):raise SystemExit(1)
  return
 runner=IngestionRunner();fast_model=FastMarketProbability();interval=max(1,int(os.getenv('PIPELINE_INTERVAL_SECONDS','60')));max_backoff=max(interval,float(os.getenv('PIPELINE_MAX_BACKOFF_SECONDS','900')));failure_streak=0;stop=threading.Event();from .main import decide,memory,markets as api_markets,runtime_config
 def request_stop(signum,frame):
  log.info('pipeline shutdown requested signal=%s',signum);stop.set()
 for signal_name in (signal.SIGINT,signal.SIGTERM):signal.signal(signal_name,request_stop)
 cleanup_interval=max(3600,int(os.getenv('RETENTION_CLEANUP_INTERVAL_SECONDS','21600')));last_cleanup=0.0
 log.info('pipeline started interval=%ss max_backoff=%ss auto_paper=%s retention_cleanup=%ss',interval,max_backoff,os.getenv('AUTO_PAPER_ENABLED','true'),cleanup_interval)
 try:
  while not stop.is_set():
   try:
    values=runtime_config.sync_process();interval=max(1,int(values.get('PIPELINE_INTERVAL_SECONDS',interval)));max_backoff=max(interval,float(values.get('PIPELINE_MAX_BACKOFF_SECONDS',max_backoff)))
    result=runner.tick(max(1,int(os.getenv('INGEST_MARKET_LIMIT','50'))));auto=autonomous_paper_cycle(runner,memory,decide,fast_model)
    if time.time()-last_cleanup>=cleanup_interval:
     cleanup=memory.cleanup_retention();last_cleanup=time.time();telemetry.inc('vesper_retention_cleanups_total');log.info('retention cleanup %s',cleanup)
    failure_streak=0;log.info('ingestion tick %s autonomous_paper=%s',result,auto)
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
