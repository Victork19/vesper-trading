import os
import hmac
import re
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Cookie
from fastapi.responses import PlainTextResponse,JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from .models import *
from .config import settings,validate_runtime_config
from .strategies import StrategyRegistry
from .secondary_signals import SecondarySignals
from .memory import TradingMemory
from .engines import EdgeEngine,RiskEngine,ScarEngine
from .metrics import MetricsEngine
from .market_data import PolymarketData
from .graph import ExperienceGraph
from .risk import PortfolioRisk,correlation_cluster
from .experimental_strategy import estimate as experimental_estimate
from .portfolio import ToxicFlowDetector, BucketKiller
from .quant import ReferenceClassEngine
from .adapters import adapter_for,paper_execution_profile,validate_execution_result
from .ingestion import IngestionStore
from .autonomy import AutonomyGate
from .observability import telemetry
from .security import SecurityManager
from .settlement import settle_decision
from .runtime_config import RuntimeConfig
from .market_policy import fast_market_allowed,fast_markets_only,fast_max_resolution_hours
from .live_execution import LiveExecutionService, VenueError
from .research_validation import exposure_notional, research_exposure, canonical_dependency_metadata
from .eda import (
    DATA_VERSION,
    OBJECTIVE_POLICY_VERSION,
    POLICY_VERSION,
    RISK_POLICY_VERSION,
    build_belief_state,
    build_episode,
    default_objective_policy,
    make_canonical_event,
)
from .attention import plan_attention
from .consequence import evaluate_actions
from .policy import propose_action
from .replay_engine import replay_episode
from .models import OpportunityObservation, ReplayRun, ModelRegistryRecord
from .model_registry import promotion_decision
from .experiential_memory import rank_memories
from datetime import datetime,timezone,timedelta
import time,uuid,json,logging,random
validate_runtime_config()
app=FastAPI(title='Vesper Trading',version='6.0.0')
@app.exception_handler(Exception)
async def safe_internal_error(request:Request,exc:Exception):
    request_id=request.headers.get('X-Request-ID') or 'unknown'
    log.exception('unhandled request failure request_id=%s path=%s',request_id,request.url.path)
    return JSONResponse({'detail':'internal_error','request_id':request_id},status_code=500,headers={'X-Request-ID':request_id})
cors_origins=[item.strip().rstrip('/') for item in os.getenv('CORS_ORIGINS','http://localhost:5173').split(',') if item.strip()]
app.add_middleware(CORSMiddleware,allow_origins=cors_origins,allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Vesper-Key','X-Request-ID','X-Vesper-CSRF'],allow_credentials=True)
log=logging.getLogger('vesper.api')

def safe_http(status:int,code:str):
 return HTTPException(status_code=status,detail=code)

@app.middleware('http')
async def request_telemetry(request:Request,call_next):
 rid=request.headers.get('X-Request-ID') or 'req_'+uuid.uuid4().hex[:12];start=time.perf_counter();status=500
 if request.url.path not in ('/health','/ready'):
  principal=None
  if request.headers.get('X-Vesper-Key'):
   try:principal=security.authenticate(request.headers.get('X-Vesper-Key'),'read')
   except HTTPException as exc:
    if exc.status_code==429:raise
  elif request.cookies.get('vesper_session'):
   try:principal=security.authenticate_session(request.cookies.get('vesper_session'),'read')
   except HTTPException:pass
  if principal:
   security.check_rate(principal.key_id,request.url.path)
   # Session creation authenticates with the API key and replaces any stale
   # session cookie. It must not be blocked by CSRF validation from that old
   # cookie, otherwise an expired session makes the user unable to log in.
   if request.cookies.get('vesper_session') and request.method in {'POST','PUT','PATCH','DELETE'} and not (request.url.path == '/auth/session' and request.method == 'POST'):
    origin=request.headers.get('origin')
    allowed={item.strip().rstrip('/') for item in os.getenv('CORS_ORIGINS','http://localhost:5173').split(',') if item.strip()}
    if not origin or origin.rstrip('/') not in allowed:
     response=JSONResponse({'detail':'Origin validation required for session state changes.'},status_code=403);status=403;return response
    csrf=request.headers.get('X-Vesper-CSRF')
    if not csrf:
     response=JSONResponse({'detail':'csrf_validation_failed'},status_code=403);status=403;return response
    try: security.validate_session_csrf(request.cookies.get('vesper_session'),csrf)
    except HTTPException as exc:
     response=JSONResponse({'detail':exc.detail},status_code=exc.status_code);status=exc.status_code;return response
  else:
   client_host=request.client.host if request.client else 'unknown'
   security.check_rate(f'unauthenticated:{client_host}',request.url.path)
 try:
  response=await call_next(request);status=response.status_code;return response
 except Exception:
  telemetry.error('unhandled_exception');raise
 finally:
  elapsed=time.perf_counter()-start;telemetry.inc('vesper_http_requests_total',labels={'method':request.method,'route':request.url.path,'status':status});telemetry.observe('vesper_http_request_duration_seconds',elapsed,labels={'method':request.method,'route':request.url.path});
  if status>=500:telemetry.error('http_5xx')
  log.info(json.dumps({'event':'http_request','request_id':rid,'method':request.method,'path':request.url.path,'status':status,'duration_ms':round(elapsed*1000,2)}))
  if 'response' in locals():response.headers['X-Request-ID']=rid

def require_api_key(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'read') if vesper_session else security.authenticate(x_vesper_key,'read')
def require_trade(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'trade') if vesper_session else security.authenticate(x_vesper_key,'trade')
def require_admin(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'admin') if vesper_session else security.authenticate(x_vesper_key,'admin')
def require_operator(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'operator') if vesper_session else security.authenticate(x_vesper_key,'operator')
def require_settlement_admin(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'settlement_admin') if vesper_session else security.authenticate(x_vesper_key,'settlement_admin')
def require_risk_admin(x_vesper_key:str|None=Header(default=None),vesper_session:str|None=Cookie(default=None)):return security.authenticate_session(vesper_session,'risk_admin') if vesper_session else security.authenticate(x_vesper_key,'risk_admin')

@app.post('/auth/session')
def create_session(request:Request,x_vesper_key:str|None=Header(default=None)):
 token,payload=security.create_session(x_vesper_key)
 response=PlainTextResponse(json.dumps({'authenticated':True,'scope':payload['scope'],'expires_at':payload['exp'],'csrf':payload['csrf']}),media_type='application/json')
 response.set_cookie('vesper_session',token,max_age=settings.session_ttl_seconds,httponly=True,secure=settings.cookie_secure or request.url.scheme=='https',samesite=settings.cookie_samesite,path='/')
 response.set_cookie('vesper_csrf',payload['csrf'],max_age=settings.session_ttl_seconds,httponly=False,secure=settings.cookie_secure or request.url.scheme=='https',samesite=settings.cookie_samesite,path='/')
 return response

@app.get('/auth/session')
def session_status(vesper_session:str|None=Cookie(default=None),x_vesper_key:str|None=Header(default=None)):
 principal=security.authenticate_session(vesper_session,'read') if vesper_session else security.authenticate(x_vesper_key,'read');return {'authenticated':True,'key_id':principal.key_id,'scope':principal.scope,'csrf':security.session_csrf(vesper_session) if vesper_session else None}

@app.delete('/auth/session')
def delete_session(vesper_session:str|None=Cookie(default=None)):
 security.revoke_session(vesper_session)
 response=PlainTextResponse('',status_code=204);response.delete_cookie('vesper_session',path='/');response.delete_cookie('vesper_csrf',path='/');return response
memory=TradingMemory();runtime_config=RuntimeConfig(memory.db);runtime_values=runtime_config.sync_process();settings.max_portfolio_heat=float(runtime_values.get('MAX_PORTFOLIO_HEAT',settings.max_portfolio_heat));security=SecurityManager(settings,memory.db);edge=EdgeEngine();risk=RiskEngine(memory);portfolio=PortfolioRisk(memory);toxic=ToxicFlowDetector();bucket_killer=BucketKiller(memory);scars=ScarEngine(memory);metrics_engine=MetricsEngine(memory);markets=PolymarketData();graph=ExperienceGraph(memory);reference=ReferenceClassEngine();strategies=StrategyRegistry();secondary=SecondarySignals();ingestion_store=IngestionStore();autonomy=AutonomyGate(memory,ingestion_store);live_execution=LiveExecutionService(memory.db)
telemetry.bind(memory.db)
if settings.live_hard_lock and memory.hot().mode==Mode.LIVE:
    live_execution.kill_switch.activate('phase 0 hard lock active; live mode forcibly halted', 'system')
    locked_hot=memory.hot();locked_hot.mode=Mode.PAPER;memory.save_hot(locked_hot);memory.event('phase0_live_lock',{'reason':'hard_lock_active'})
def live_evidence_checks():
 report=research_report(memory.decisions());oos=report.get('out_of_sample') or {};minimum=max(10,int(os.getenv('RESEARCH_MIN_OOS_BUCKETS','10')));minimum_markets=max(10,int(os.getenv('LIVE_MIN_OOS_MARKETS','20')));min_expectancy=float(os.getenv('LIVE_MIN_OOS_EXPECTANCY','0'));min_brier_lift=float(os.getenv('LIVE_MIN_BRIER_LIFT','0'));min_log_loss_lift=float(os.getenv('LIVE_MIN_LOG_LOSS_LIFT','0'))
 return {'research_available':report.get('status')=='available','oos_sample':int(oos.get('count') or 0)>=minimum,'oos_markets':int(oos.get('unique_markets') or 0)>=minimum_markets,'oos_expectancy':oos.get('expectancy_ci_low') is not None and float(oos['expectancy_ci_low'])>min_expectancy,'oos_brier_lift':oos.get('brier_lift_ci_low') is not None and float(oos['brier_lift_ci_low'])>min_brier_lift,'oos_log_loss_lift':oos.get('log_loss_lift_ci_low') is not None and float(oos['log_loss_lift_ci_low'])>min_log_loss_lift,'oos_clv':oos.get('clv_ci_low') is not None and float(oos['clv_ci_low'])>0,'eda_integrity':memory.eda_integrity().get('healthy',False)}
def live_execution_reconciled():
 try:
  result=live_execution.readiness()
  return bool(result.get('ready')) and bool(memory.db.reconciliation_is_healthy())
 except Exception:return False
def live_approval_active():
 approval=memory.get('HOT','live_approval') or {}
 if not approval.get('active'):return False
 try:
  expires=datetime.fromisoformat(str(approval['expires_at']).replace('Z','+00:00'))
  fingerprint=f"{settings.max_capital}:{settings.max_order_size}:{settings.deployment_stage}"
  return datetime.now(timezone.utc)<expires and approval.get('config_fingerprint')==fingerprint and len(approval.get('approvers') or [])>=int(approval.get('required_approvers',1))
 except (KeyError,TypeError,ValueError):
  return False
@app.get('/health')
def health():telemetry.set('vesper_mode',{'paper':0,'shadow':1,'live':2}.get(memory.hot().mode.value,0));return {'status':'ok','service':'vesper-trading','mode':memory.hot().mode,'memory_load_bearing':True,'database':'postgresql','live_enabled':settings.live_enabled}
def _readiness_payload():
 q=ingestion_store.quality();h=memory.hot();approval=memory.get('HOT','live_approval') or {};worker=ingestion_store.worker_health()
 decisions=memory.decisions();sample_keys={f'{d.strategy_id}:{d.market_id}:{d.regime}:{d.model_version or "none"}' for d in decisions if d.research_eligible and d.size>0 and d.paper_fill_fraction>0 and d.outcome!='pending'}
 live_checks={'auth_configured':bool(settings.api_key and settings.admin_key),'limits_configured':settings.max_capital>0 and settings.max_order_size>0,'sample_gate':len(sample_keys)>=settings.min_sample,'data_quality':q['score']>=settings.min_data_quality and q.get('book_coverage',0)>=settings.min_data_quality and not q['stale'],'operator_approved':live_approval_active(),'execution_reconciled':live_execution_reconciled(),**live_evidence_checks()}
 checks={'api':True,'memory':memory.db.ping(),'worker':not worker.get('stale',True),'data_quality':q['score']>=settings.min_data_quality or h.mode==Mode.PAPER,'data_fresh':not q['stale'] or h.mode==Mode.PAPER,'live_safe':all(live_checks) if h.mode==Mode.LIVE else True};return {'ready':all(checks.values()),'checks':checks,'live_checks':live_checks,'quality':q,'worker':worker}
@app.get('/ready')
def ready():
 payload=_readiness_payload()
 return payload if payload["ready"] else JSONResponse(
    content=jsonable_encoder(payload),
    status_code=503,
)
@app.get('/state/hot',response_model=HotState)
def hot(_=Depends(require_api_key)):return memory.hot()
@app.get('/constitution')
def constitution(_=Depends(require_api_key)):return memory.get('REFERENCE','constitution')
@app.get('/orders',response_model=list[OrderRecord])
def list_orders(_=Depends(require_api_key)):return memory.orders()
@app.get('/orders/{order_id}',response_model=OrderRecord)
def get_order(order_id:str,_=Depends(require_api_key)):
 order=memory.order(order_id)
 if not order:raise HTTPException(404,'Order not found')
 return order
@app.post('/orders/{order_id}/reconcile',response_model=OrderRecord)
def reconcile_order(order_id:str,_=Depends(require_operator)):
 order=memory.order(order_id)
 if not order:raise HTTPException(404,'Order not found')
 if order.mode!=Mode.LIVE:raise HTTPException(409,'Only live orders require venue reconciliation.')
 result=live_execution.reconcile(order);status=result.get('status')
 if status in {x.value for x in OrderStatus}:
  from .worker import _atomic_apply_reconciliation
  _atomic_apply_reconciliation(memory,order,result)
 else: order.error='reconciliation_required';memory.save_order(order)
 memory.event('order_reconciled',{'order_id':order.id,'result':result});return order
@app.post('/orders/{order_id}/cancel',response_model=OrderRecord)
def cancel_order(order_id:str,_=Depends(require_operator)):
 order=memory.order(order_id)
 if not order:raise HTTPException(404,'Order not found')
 if order.mode!=Mode.LIVE:raise HTTPException(409,'Only live orders can be canceled at the venue.')
 try: result=live_execution.cancel(order)
 except VenueError as exc: log.warning('order cancellation failed order=%s category=%s',order_id,getattr(exc,'category','venue')); raise safe_http(503,'venue_cancellation_unavailable')
 status=result.get('status')
 if status in {x.value for x in OrderStatus}:
  from .worker import _atomic_apply_reconciliation
  _atomic_apply_reconciliation(memory,order,result)
 memory.event('order_canceled',{'order_id':order.id,'result':result});return memory.order(order_id)
@app.get('/strategies')
def strategy_list(_=Depends(require_api_key)):return [x.__dict__ for x in strategies.all()]
@app.get('/scars',response_model=list[Scar])
def list_scars(_=Depends(require_api_key)):return memory.scars()
@app.get('/principles',response_model=list[Principle])
def list_principles(_=Depends(require_api_key)):return memory.principles()
@app.get('/memory/digest')
def memory_digest(strategy_id:str='reference_class',market_type:str='unknown',market_id:str='unknown',regime:str='baseline',_=Depends(require_api_key)):
 return memory.memory_digest(strategy_id,market_type,market_id,regime)
@app.get('/decisions',response_model=list[DecisionRecord])
def list_decisions(_=Depends(require_api_key)):return memory.decisions()
@app.get('/metrics',response_model=list[ProcessSnapshot])
def metrics(_=Depends(require_api_key)):return memory.snapshots()
@app.get('/replay/{decision_id}')
def replay(decision_id,_=Depends(require_api_key)):
 x=memory.replay(decision_id)
 if not x:raise HTTPException(404,'Decision not found')
 if isinstance(x,dict) and x.get('snapshot_hash'):
  x=dict(x);x['verified_market_input']=ingestion_store.verified_input(x.get('snapshot_hash'))
 return x
@app.get('/episodes/{episode_id}')
def get_episode(episode_id:str,_=Depends(require_api_key)):
 episode=memory.episode(episode_id)
 if not episode:raise HTTPException(404,'Episode not found')
 events=memory.eda_events(episode_id)
 payload=episode.model_dump(mode='json');payload['events']=events
 payload['latest_outcome']=next((item['payload'] for item in reversed(events) if item['event_type']=='outcome_resolved'),None)
 payload['latest_attribution']=next((item['payload'] for item in reversed(events) if item['event_type']=='outcome_attribution'),None)
 return payload
@app.get('/episodes/{episode_id}/events')
def episode_events(episode_id:str,limit:int=200,_=Depends(require_api_key)):
 if not memory.episode(episode_id):raise HTTPException(404,'Episode not found')
 return memory.eda_events(episode_id,max(1,min(limit,1000)))
@app.post('/eda/information-requests/{request_id}/outcome')
def information_request_outcome(request_id:str,payload:dict,principal=Depends(require_operator)):
 episode_id=payload.get('episode_id')
 if episode_id and not memory.episode(episode_id):raise HTTPException(404,'Episode not found')
 status=str(payload.get('status') or 'unknown')
 if status not in {'fulfilled','rejected','timed_out','failed'}:raise HTTPException(422,'invalid_information_request_status')
 event=make_canonical_event('attention','information_request_outcome',{'request_id':request_id,**payload},episode_id=episode_id,source_event_id=request_id)
 memory.append_eda_event(event);memory.event('eda_information_request_outcome',{'request_id':request_id,'status':status,'actor':principal.key_id});telemetry.inc('vesper_information_request_outcomes_total',labels={'status':status});return event
@app.get('/eda/health')
def eda_health(_=Depends(require_api_key)):
 return {'integrity':memory.eda_integrity(),'opportunities':len(memory.opportunities()),'replay_runs':len(memory.replay_runs()),'models':len(memory.model_registry()),'telemetry':telemetry.snapshot()}
@app.post('/operator/retention/cleanup')
def retention_cleanup(principal=Depends(require_admin)):
  result=memory.cleanup_retention();memory.event('retention_cleanup',{'actor':principal.key_id,'result':result});return result
@app.get('/eda/telemetry')
def eda_telemetry(format:str='json',_=Depends(require_api_key)):
 if format == 'prometheus': return PlainTextResponse(telemetry.prometheus(),media_type='text/plain; version=0.0.4')
 return telemetry.snapshot()
@app.post('/eda/opportunities')
def record_opportunity(opportunity:OpportunityObservation,principal=Depends(require_operator)):
 memory.save_opportunity(opportunity);memory.event('eda_opportunity_recorded',{'opportunity_id':opportunity.opportunity_id,'status':opportunity.status,'actor':principal.key_id});telemetry.inc('vesper_opportunities_total',labels={'status':opportunity.status});return opportunity
@app.get('/eda/opportunities')
def list_opportunities(limit:int=200,_=Depends(require_api_key)):return memory.opportunities(limit)
@app.post('/eda/replay/{episode_id}')
def run_eda_replay(episode_id:str,mode:str='historical',as_of:str|None=None,_=Depends(require_api_key)):
 episode=memory.episode(episode_id)
 if not episode:raise HTTPException(404,'Episode not found')
 current_objective=ObjectivePolicy.model_validate(memory.get('REFERENCE','objective_policy') or default_objective_policy().model_dump())
 result=replay_episode(episode,memory.eda_events(episode_id),mode=mode,as_of=as_of,objective_policy=current_objective)
 memory.save_replay(ReplayRun(replay_id=result['replay_id'],episode_id=episode_id,mode=mode,as_of=result['as_of'],result=result))
 telemetry.inc('vesper_replay_runs_total',labels={'mode':mode,'mismatch':str(result['mismatch']).lower()})
 return result
@app.get('/eda/replays')
def list_eda_replays(episode_id:str|None=None,limit:int=100,_=Depends(require_api_key)):return memory.replay_runs(episode_id,limit)
@app.post('/eda/models')
def register_eda_model(record:ModelRegistryRecord,principal=Depends(require_admin)):
 decision=promotion_decision(record,operator=getattr(principal,'key_id',None))
 record.status=decision['status'];record.approved_by=decision['approved_by'];memory.save_model_registry(record)
 return {'record':record,'promotion':decision}
@app.get('/eda/models')
def list_eda_models(_=Depends(require_api_key)):return memory.model_registry()
@app.get('/eda/memory/search')
def search_eda_memory(query:str,limit:int=8,_=Depends(require_api_key)):
 memories=memory.all('semantic')+memory.all('procedural')+memory.all('failure')+memory.all('model')
 return rank_memories(memories,query,limit)
@app.get('/eda/objective')
def eda_objective(_=Depends(require_api_key)):
 return memory.get('REFERENCE','objective_policy') or default_objective_policy().model_dump()
@app.get('/graph')
def graph_edges(_=Depends(require_api_key)):return graph.edges()
@app.get('/audit')
def audit(limit:int=200,_=Depends(require_api_key)):return memory.audit(max(1,min(limit,1000)))
@app.get('/risk')
def risk_state(_=Depends(require_api_key)):
 realized_pnl=sum(float(decision.pnl or 0) for decision in memory.decisions() if decision.outcome in ('win','loss','push'))
 return {'portfolio_heat':portfolio.heat(),'max_portfolio_heat':settings.max_portfolio_heat,'realized_pnl':realized_pnl,'daily_pnl':memory.hot().daily_pnl,'weekly_pnl':memory.hot().weekly_pnl,'correlation_regime':memory.hot().correlation_regime}
@app.get('/dashboard')
def dashboard(_=Depends(require_api_key)):
 h=memory.hot();return {'mode':h.mode,'risk':risk_state(),'pipeline':autonomy.status(),'observations':ingestion_store.status(),'decisions':len(memory.decisions()),'scars':len(memory.scars()),'principles':len(memory.principles()),'metrics':len(memory.snapshots())}
@app.get('/markets')
def list_markets(limit:int=20,fast_only:bool=False):
 try:
  if fast_only:
   now=datetime.now(timezone.utc);minimum_hours=max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05')));maximum_hours=max(minimum_hours,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')))
   return markets.markets(limit,order='endDate',ascending=True,closed=False,end_date_min=(now+timedelta(hours=minimum_hours)).isoformat().replace('+00:00','Z'),end_date_max=(now+timedelta(hours=maximum_hours)).isoformat().replace('+00:00','Z'))
  return markets.markets(limit)
 except Exception as e:log.warning('market list failed error=%s',e);raise HTTPException(502,'market_data_unavailable')
@app.get('/markets/{market_id}')
def get_market(market_id):
 try:return markets.market(market_id)
 except Exception as e:log.warning('market lookup failed market=%s error=%s',market_id,e);raise HTTPException(502,'market_data_unavailable')
@app.get('/markets/input/{market_id}',response_model=MarketInput)
def market_input(market_id:str,_=Depends(require_api_key)):
 try:
  market=markets.market(market_id);yes_token,no_token=markets.token_pair(market)
  yes_book=markets.book(yes_token) if yes_token else None;no_book=markets.book(no_token) if no_token else None
  result=markets.to_input(market,yes_book=yes_book,no_book=no_book);ingestion_store.save_verified_input(result);return result
 except Exception as e:log.warning('market input failed market=%s error=%s',market_id,e);raise HTTPException(502,'market_data_unavailable')
@app.get('/markets/book/{token_id}')
def get_book(token_id):
 try:return markets.book(token_id)
 except Exception as e:log.warning('book lookup failed token=%s error=%s',token_id,e);raise HTTPException(502,'market_data_unavailable')
@app.get('/markets/quality/{market_id}',response_model=MarketQuality)
def market_quality(market_id:str,token_id:str|None=None,_=Depends(require_api_key)):
 try:
  market=markets.market(market_id);book=markets.book(token_id) if token_id else None;return markets.quality(market,book)
 except Exception as e:log.warning('market quality failed market=%s error=%s',market_id,e);raise HTTPException(502,'market_data_unavailable')
@app.post('/signals')
def signals(payload:dict,_=Depends(require_trade)):return secondary.analyze(payload.get('question',''),payload.get('context',{}))
@app.get('/operations')
def operations(_=Depends(require_api_key)):return {'mode':memory.hot().mode,'deployment_stage':settings.deployment_stage,'live_enabled':settings.live_enabled,'max_capital':settings.max_capital,'max_order_size':settings.max_order_size,'database':'postgresql','kill_switch':memory.hot().daily_pnl<=-.1 or memory.hot().weekly_pnl<=-.2,'live_execution':live_execution.readiness()}
@app.get('/operator/deployment-status')
def deployment_status(_=Depends(require_admin)):
 return {'stage':settings.deployment_stage,'live_hard_lock':settings.live_hard_lock,'live_enabled':settings.live_enabled,'canary_max_capital':settings.canary_max_capital,'controlled_account_required':settings.deployment_stage in {'controlled','canary','production'},'capital_deployment_allowed':settings.live_enabled and not settings.live_hard_lock and settings.deployment_stage in {'canary','production'}}
@app.get('/operator/live-readiness')
def operator_live_readiness(_=Depends(require_admin)):
 account=live_execution.verify_account() if live_execution.venue else {'verified':False,'code':'venue_client_unavailable'}
 if not account.get('verified'): account={'verified':False,'code':'account_verification_failed'}
 return {'execution':live_execution.readiness(),'account':account,'statistical':live_evidence_checks()}
@app.get('/operator/kill-switch')
def get_kill_switch(_=Depends(require_admin)):return live_execution.kill_switch.status()
@app.post('/operator/kill-switch/activate')
def activate_kill_switch(payload:dict|None=None,principal=Depends(require_operator)):
 reason=str((payload or {}).get('reason') or 'operator emergency stop');actor=getattr(principal,'key_id','admin');live_execution.kill_switch.activate(reason,actor);memory.event('kill_switch',{'active':True,'reason':reason,'actor':actor});return live_execution.kill_switch.status()
@app.post('/operator/kill-switch/release')
def release_kill_switch(principal=Depends(require_operator)):
 status=live_execution.kill_switch.status()
 if status.get('phase')!='HALTED' or not status.get('active',True): raise safe_http(409,'kill_switch_not_verified')
 if any(circuit.state().state=='open' for circuit in (live_execution.submission_circuit,live_execution.reconciliation_circuit,live_execution.cancellation_circuit,live_execution.account_circuit)):raise safe_http(409,'execution_circuit_open')
 try:
  readiness=live_execution.readiness()
  if not readiness.get('checks',{}).get('venue_healthy',False):raise safe_http(409,'venue_health_unverified')
  if not readiness.get('checks',{}).get('account_verified',False):raise safe_http(409,'account_reconciliation_unverified')
  if live_execution.venue is None or live_execution.venue.list_open_orders():raise safe_http(409,'venue_open_orders_remain')
 except HTTPException: raise
 except Exception as exc:
  log.warning('kill switch release verification failed error=%s',exc)
  raise safe_http(503,'kill_switch_verification_unavailable')
 terminal={OrderStatus.FILLED,OrderStatus.CANCELED,OrderStatus.EXPIRED,OrderStatus.REJECTED,OrderStatus.FAILED}
 if any(order.mode==Mode.LIVE and order.status not in terminal for order in memory.orders()):raise safe_http(409,'internal_live_orders_unresolved')
 if getattr(memory,'db',None):
  with memory.db.connection() as c:
   unresolved=c.execute("SELECT 1 FROM execution_attempts WHERE action='intent' AND status IN ('pending','uncertain','accepted','filled','partially_filled','reconciliation_required') LIMIT 1").fetchone()
  if unresolved:raise safe_http(409,'uncertain_submissions_remain')
 actor=getattr(principal,'key_id','admin');live_execution.kill_switch.release(actor);memory.event('kill_switch',{'active':False,'actor':actor,'verified_zero_exposure':True});return live_execution.kill_switch.status()
@app.get('/pipeline/status')
def pipeline_status(_=Depends(require_api_key)):return autonomy.status()
@app.get('/pipeline/observations')
def pipeline_observations(_=Depends(require_api_key)):return ingestion_store.status()
@app.get('/readiness')
def readiness(_=Depends(require_api_key)):return _readiness_payload()
@app.get('/readiness/summary')
def readiness_summary(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();decisions=memory.decisions();exposed=[d for d in decisions if d.size>0 and d.paper_fill_fraction>0];research_exposed=[d for d in exposed if d.research_eligible];exploration_exposed=[d for d in exposed if (d.market_context or {}).get('paper_exploration') is True];exploration_resolved=[d for d in exploration_exposed if d.outcome!='pending'];exploration_pending=[d for d in exploration_exposed if d.outcome=='pending'];exploration_pnl=sum(float(d.pnl) for d in exploration_resolved);resolved=[d for d in research_exposed if d.outcome!='pending'];pending=[d for d in research_exposed if d.outcome=='pending'];key=lambda d:f'{d.strategy_id}:{d.market_id}:{d.regime}:{d.model_version or "none"}';resolved_keys={key(d) for d in resolved};pending_keys={key(d) for d in pending};snapshots=memory.snapshots();resolved_count=len(resolved);wins=sum(1 for d in resolved if d.outcome=='win');pnl=sum(float(d.pnl) for d in resolved);minimum=settings.min_sample;blockers=[]
 if len(resolved_keys)<minimum:blockers.append(f'Need {minimum-len(resolved_keys)} more independent resolved paper outcomes before the live sample gate can pass.')
 if q['score']<settings.min_data_quality or q.get('book_coverage',0)<settings.min_data_quality or q['stale']:blockers.append('Market data must remain fresh, valid, and sufficiently quote-covered.')
 if worker.get('stale'):blockers.append('Ingestion worker heartbeat is stale or missing.')
 if not settings.live_enabled:blockers.append('LIVE_TRADING_ENABLED is false.')
 if settings.max_capital<=0 or settings.max_order_size<=0:blockers.append('Live capital and order limits are not configured.')
 if not live_approval_active():blockers.append('Explicit operator live approval is missing or expired.')
 for evidence,passed in live_evidence_checks().items():
  if not passed:blockers.append(f'Live statistical gate has not passed: {evidence}.')
 if not live_execution_reconciled():blockers.append('Authenticated Polymarket CLOB execution, wallet verification, or venue reconciliation is not ready.')
 learning='collecting' if not research_exposed else 'learning' if len(resolved_keys)<minimum else 'evidence_ready'
 return {'status':'paper_learning','learning_status':learning,'summary':f'{len(exposed)} exposed paper decisions ({len(exploration_exposed)} exploration-only; {len(research_exposed)} research-eligible); {len(resolved_keys)} research outcomes resolved; {len(pending_keys)} awaiting settlement.','automation':{'enabled':os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true','decisions_per_tick':max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3'))),'exploration_enabled':os.getenv('AUTO_PAPER_EXPLORATION_ENABLED','true').lower()=='true','exploration_max_per_tick':max(0,int(os.getenv('AUTO_PAPER_EXPLORATION_MAX_PER_TICK','2'))),'exploration_size':max(.0001,float(os.getenv('AUTO_PAPER_EXPLORATION_SIZE','.01'))),'cooldown_seconds':max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600'))),'min_resolution_hours':max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05'))),'max_resolution_hours':min(max(.01,float(os.getenv('AUTO_PAPER_MAX_RESOLUTION_HOURS','24'))),max(.01,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')))),'fast_markets_only':os.getenv('FAST_MARKETS_ONLY','true').lower()=='true','fast_max_resolution_hours':max(.01,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1'))),'prefer_fast_markets':True},'paper':{'decisions':len(decisions),'exposed':len(exposed),'exploration_exposed':len(exploration_exposed),'exploration_resolved':len(exploration_resolved),'exploration_pending':len(exploration_pending),'exploration_pnl':exploration_pnl,'research_exposed':len(research_exposed),'independent_buckets':len({key(d) for d in research_exposed}),'resolved':resolved_count,'independent_resolved':len(resolved_keys),'pending':len(pending),'independent_pending':len(pending_keys),'wins':wins,'win_rate':wins/resolved_count if resolved_count else None,'pnl':pnl,'metrics_buckets':len(snapshots),'minimum_sample':minimum},'research':research_report(decisions),'data':{'snapshots':q['snapshots'],'minimum_snapshots':int(os.getenv('MIN_MARKET_SNAPSHOTS','1000')),'quality':q['score'],'book_coverage':q.get('book_coverage',0),'stale':q['stale']},'worker':{'status':worker.get('status'),'last_resolved':worker.get('last_resolved',0),'last_pending':worker.get('last_pending',0)},'live':{'eligible':False,'blockers':blockers}}

def _research_probability(decision):
 value=decision.model_probability if decision.model_version and decision.model_probability is not None else decision.fair_probability
 return max(.0001,min(.9999,float(value)))

def event_family(question: str, market_type: str = 'unknown') -> str:
    """Stable dependency cluster for related markets, horizons, and quotes."""
    text=re.sub(r'\b\d+(?:\.\d+)?\b','<number>',str(question or '').lower())
    text=re.sub(r'\s+',' ',text).strip()
    return f'{market_type}:{text}'

def _research_slice(items):
 if not items:return {'count':0,'wins':0,'win_rate':None,'pnl':0.0,'expectancy':None,'expectancy_ci_low':None,'expectancy_ci_high':None,'profit_factor':None,'max_drawdown':0.0,'avg_clv':None,'clv_ci_low':None,'clv_ci_high':None,'brier':None,'log_loss':None,'market_brier':None,'market_log_loss':None,'brier_lift_vs_market':None,'brier_lift_ci_low':None,'brier_lift_ci_high':None,'log_loss_lift_vs_market':None,'log_loss_lift_ci_low':None,'log_loss_lift_ci_high':None,'calibration_error':None,'avg_edge':None,'cost_drag':0.0,'unique_markets':0}
 wins=sum(1 for d in items if d.outcome=='win');pnl=sum(float(d.pnl) for d in items);profits=sum(max(0,float(d.pnl)) for d in items);losses=sum(min(0,float(d.pnl)) for d in items);equity=peak=drawdown=0.0
 for d in items:
  equity+=float(d.pnl);peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
 scored=[d for d in items if d.resolved_yes is not None];brier=sum((_research_probability(d)-(1.0 if d.resolved_yes else 0.0))**2 for d in scored)/len(scored) if scored else None
 log_loss=sum(-(1.0 if d.resolved_yes else 0.0)*math.log(_research_probability(d))-(0.0 if d.resolved_yes else 1.0)*math.log(1-_research_probability(d)) for d in scored)/len(scored) if scored else None
 market_scored=[d for d in scored if 0<d.price<1];market_brier=sum((d.price-(1.0 if d.resolved_yes else 0.0))**2 for d in market_scored)/len(market_scored) if market_scored else None
 market_log_loss=sum(-(1.0 if d.resolved_yes else 0.0)*math.log(max(.0001,min(.9999,d.price)))-(0.0 if d.resolved_yes else 1.0)*math.log(max(.0001,min(.9999,1-d.price))) for d in market_scored)/len(market_scored) if market_scored else None
 calibration_error=sum(abs(_research_probability(d)-(1.0 if d.resolved_yes else 0.0)) for d in scored)/len(scored) if scored else None
 def ci(values, clusters=None):
  if not values:return (None,None)
  mean=sum(values)/len(values)
  if len(values)<2:return (mean,mean)
  rng=random.Random(1729+len(values));draws=[];count=max(200,min(2000,int(os.getenv('RESEARCH_BOOTSTRAP_SAMPLES','1000'))))
  if clusters and len(clusters)==len(values):
   grouped={};
   for value,cluster in zip(values,clusters):grouped.setdefault(cluster,[]).append(value)
   groups=list(grouped.values())
   for _ in range(count):
    sample=[item for _ in groups for item in groups[rng.randrange(len(groups))]];draws.append(sum(sample)/len(sample))
  else:
   for _ in range(count):draws.append(sum(values[rng.randrange(len(values))] for _ in values)/len(values))
  draws.sort();return (draws[max(0,int(.025*len(draws)))],draws[min(len(draws)-1,int(.975*len(draws)))])
 event_clusters=[str(d.market_context.get('event_family') or d.market_id) for d in items]
 expectancy_low,expectancy_high=ci([float(d.pnl) for d in items],event_clusters)
 brier_lifts=[(d.price-(1.0 if d.resolved_yes else 0.0))**2-(_research_probability(d)-(1.0 if d.resolved_yes else 0.0))**2 for d in market_scored]
 log_lifts=[(-(1.0 if d.resolved_yes else 0.0)*math.log(max(.0001,min(.9999,d.price)))-(0.0 if d.resolved_yes else 1.0)*math.log(max(.0001,min(.9999,1-d.price))))-(-(1.0 if d.resolved_yes else 0.0)*math.log(_research_probability(d))-(0.0 if d.resolved_yes else 1.0)*math.log(1-_research_probability(d))) for d in market_scored]
 brier_low,brier_high=ci(brier_lifts,[str(d.market_context.get('event_family') or d.market_id) for d in items if d in market_scored]);log_low,log_high=ci(log_lifts,[str(d.market_context.get('event_family') or d.market_id) for d in items if d in market_scored]);clvs=[float(d.clv) for d in items if d.clv is not None];clv_clusters=[str(d.market_context.get('event_family') or d.market_id) for d in items if d.clv is not None];clv_low,clv_high=ci(clvs,clv_clusters)
 costs=sum(float(d.executed_fees) if d.mode==Mode.LIVE else float(d.paper_cost) for d in items);return {'count':len(items),'wins':wins,'win_rate':wins/len(items),'pnl':pnl,'expectancy':pnl/len(items),'expectancy_ci_low':expectancy_low,'expectancy_ci_high':expectancy_high,'profit_factor':profits/abs(losses) if losses else None,'max_drawdown':drawdown,'avg_clv':sum(clvs)/len(clvs) if clvs else None,'clv_ci_low':clv_low,'clv_ci_high':clv_high,'brier':brier,'log_loss':log_loss,'market_brier':market_brier,'market_log_loss':market_log_loss,'brier_lift_vs_market':market_brier-brier if market_brier is not None and brier is not None else None,'brier_lift_ci_low':brier_low,'brier_lift_ci_high':brier_high,'log_loss_lift_vs_market':market_log_loss-log_loss if market_log_loss is not None and log_loss is not None else None,'log_loss_lift_ci_low':log_low,'log_loss_lift_ci_high':log_high,'calibration_error':calibration_error,'avg_edge':sum(float(d.edge) for d in items)/len(items),'cost_drag':costs,'unique_markets':len({d.market_id for d in items}),'independent_event_families':len(set(event_clusters))}

def _research_exposure(d):
 # Research must measure collateral exposure, not contract count.
 return research_exposure(d) if d.execution_reconciled else 0.0

def research_report(decisions):
 fast_only=fast_markets_only();fast_max=fast_max_resolution_hours()
 minimum_buckets=max(30,int(os.getenv('RESEARCH_MIN_INDEPENDENT_BUCKETS','30')))
 eligible=[d for d in decisions if d.research_eligible and _research_exposure(d)>0 and d.execution_reconciled and d.status not in ('execution-failed','execution_failed') and d.outcome in ('win','loss','push') and d.resolved_yes is not None and (not fast_only or float(d.market_context.get('resolution_hours',999999))<=fast_max)]
 versioned=[d for d in eligible if d.model_version]
 active_model_version=max(versioned,key=lambda d:d.created_at).model_version if versioned else None
 eligible=[d for d in eligible if d.model_version==active_model_version] if active_model_version else [d for d in eligible if not d.model_version]
 excluded_slow=sum(1 for d in decisions if d.research_eligible and d.size>0 and d.paper_fill_fraction>0 and d.outcome in ('win','loss','push') and fast_only and float(d.market_context.get('resolution_hours',999999))>fast_max)
 raw=sorted(eligible,key=lambda d:d.created_at);groups={}
 for decision in raw:groups.setdefault(f'{decision.strategy_id}:{decision.market_context.get("event_family",decision.market_id)}:{decision.regime}:{decision.model_version or "none"}',[]).append(decision)
 resolved=[]
 for bucket in groups.values():
  first=bucket[0];pnl=sum(float(d.pnl) for d in bucket);clvs=[float(d.clv) for d in bucket if d.clv is not None];weights=[max(1e-9,_research_exposure(d)) for d in bucket]
  def weighted(field):
   values=[getattr(d,field) for d in bucket];available=[(weight,float(value)) for weight,value in zip(weights,values) if value is not None]
   return sum(weight*value for weight,value in available)/sum(weight for weight,_ in available) if available else None
  weighted_price=weighted('price');weighted_fair=weighted('fair_probability')
  resolved.append(first.model_copy(update={'price':weighted_price if weighted_price is not None else first.price,'fair_probability':weighted_fair if weighted_fair is not None else first.fair_probability,'raw_model_probability':weighted('raw_model_probability'),'model_probability':weighted('model_probability'),'pnl':pnl,'clv':sum(clvs)/len(clvs) if clvs else None,'outcome':'win' if pnl>0 else 'loss' if pnl<0 else 'push'}))
 resolved.sort(key=lambda d:d.created_at);split=max(0,int(len(resolved)*.7));train=resolved[:split];test=resolved[split:]
 # A market is the unit of information. Repeated quotes are exposures, not
 # independent evidence. The one-bucket embargo prevents the first OOS bucket
 # from sharing the same immediate market state as the training tail.
 embargo=int(os.getenv('RESEARCH_EMBARGO_BUCKETS','1'))
 embargo_items=test[:embargo];test=test[embargo:]
 bins=[]
 for lower in (0,.2,.4,.6,.8):
  upper=lower+.2;bucket=[d for d in resolved if lower<=_research_probability(d)<(upper if upper<1 else 1.0001)]
  if bucket:bins.append({'range':[lower,min(1,upper)],'count':len(bucket),'predicted':sum(_research_probability(d) for d in bucket)/len(bucket),'actual':sum(1 for d in bucket if d.resolved_yes is True)/len(bucket)})
 scars=memory.scars();post=[]
 for scar in scars:
  try:created=datetime.fromisoformat(scar.created_at.replace('Z','+00:00'))
  except ValueError:continue
  bucket=[d for d in resolved if d.strategy_id==scar.strategy_id and d.market_type==scar.market_type and d.regime==scar.regime and datetime.fromisoformat(d.created_at.replace('Z','+00:00'))>created]
  if bucket:post.append({'scar_id':scar.id,'status':scar.status,'outcomes':len(bucket),'pnl':sum(float(d.pnl) for d in bucket),'win_rate':sum(1 for d in bucket if d.outcome=='win')/len(bucket)})
 warnings=[]
 if len(resolved)<minimum_buckets:warnings.append('insufficient_independent_outcomes')
 if len(test)<max(10,int(os.getenv('RESEARCH_MIN_OOS_BUCKETS','10'))):warnings.append('insufficient_out_of_sample_buckets')
 return {'status':'insufficient_data' if len(resolved)<minimum_buckets else 'available','model_version':active_model_version,'fast_markets_only':fast_only,'fast_max_resolution_hours':fast_max,'excluded_slow_resolved_exposures':excluded_slow,'resolved_exposures':len(raw),'independent_buckets':len(resolved),'independent_resolved':len(resolved),'minimum_for_oos':minimum_buckets,'split_method':'chronological_market_bucket_70_30_with_embargo','embargoed_buckets':len(embargo_items),'calibration_bins':bins,'warnings':warnings,'train':_research_slice(train),'out_of_sample':_research_slice(test),'scar_effectiveness':post}

@app.get('/research/report')
def research(_=Depends(require_api_key)):return research_report(memory.decisions())
@app.get('/metrics/prometheus',response_class=PlainTextResponse)
def prometheus_metrics(_=Depends(require_api_key)):return telemetry.prometheus()
@app.get('/observability')
def observability(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();telemetry.set('vesper_ingestion_quality_score',q['score']);telemetry.set('vesper_ingestion_stale',int(q['stale']));telemetry.set('vesper_worker_stale',int(worker.get('stale',True)));return {'telemetry':telemetry.snapshot(),'ingestion':q,'worker':worker,'ready':_readiness_payload()}
@app.get('/alerts')
def alerts(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();snap=telemetry.snapshot();h=memory.hot();items=[]
 if q['stale']:items.append({'severity':'critical','code':'MARKET_DATA_STALE','message':'No fresh market observations within the freshness window.'})
 if q['score']<settings.min_data_quality:items.append({'severity':'warning','code':'MARKET_DATA_QUALITY_LOW','message':f"Market-data quality is {q['score']:.3f}."})
 if h.mode!=Mode.PAPER and q.get('book_coverage',0)<settings.min_data_quality:items.append({'severity':'warning','code':'MARKET_BOOK_COVERAGE_LOW','message':f"Executable YES/NO ask coverage is {q.get('book_coverage',0):.3f}."})
 if worker.get('stale'):items.append({'severity':'critical','code':'INGESTION_WORKER_STALE','message':'The ingestion worker has not reported a successful heartbeat recently.'})
 if snap['recent_errors_5m']>=5:items.append({'severity':'critical','code':'ERROR_BURST','message':f"{snap['recent_errors_5m']} errors observed in five minutes."})
 return {'active':items,'count':len(items),'generated_at':now_iso()}
@app.post('/mode/{mode}',response_model=HotState)
def set_mode(mode:Mode,_=Depends(require_admin)):
 h=memory.hot()
 if mode==Mode.LIVE:
  if not settings.live_enabled or not live_approval_active():raise HTTPException(403,'Live mode requires a current explicit operator approval and LIVE_TRADING_ENABLED=true.')
  if not live_execution_reconciled():raise HTTPException(403,'Live mode is unavailable until authenticated execution and venue reconciliation are production-enabled.')
  evidence=live_evidence_checks()
  if not all(evidence.values()):raise HTTPException(403,'Live mode requires positive out-of-sample evidence and improvement over the market baseline.')
 if mode!=Mode.LIVE and h.mode==Mode.LIVE:
  memory.put('HOT','live_approval',{'active':False,'revoked_at':now_iso(),'reason':'left_live_mode'})
  live_execution.kill_switch.activate('live mode exited; reconcile outstanding venue orders', 'system')
 h.mode=mode;memory.save_hot(h);memory.event('mode_changed',{'mode':mode});return h
@app.post('/operator/request-live')
def request_live(payload:dict,principal=Depends(require_operator)):
 ttl=max(60,min(int(os.getenv('LIVE_APPROVAL_TTL_SECONDS','900')),int(settings.privileged_session_ttl_seconds)))
 now=datetime.now(timezone.utc);expires=(now+timedelta(seconds=ttl)).isoformat().replace('+00:00','Z');actor=getattr(principal,'key_id','unknown');fingerprint=f"{settings.max_capital}:{settings.max_order_size}:{settings.deployment_stage}"
 dual=settings.require_dual_live_approval and settings.deployment_stage in {'controlled','canary','production'}
 existing=memory.get('HOT','live_approval') or {}
 if dual and existing.get('pending_second_approval'):
  try:
   pending_expires=datetime.fromisoformat(str(existing.get('expires_at','')).replace('Z','+00:00'))
  except (TypeError,ValueError): pending_expires=now-timedelta(seconds=1)
  if pending_expires<=now: raise safe_http(409,'live_approval_expired')
  if actor in (existing.get('approvers') or []):raise safe_http(409,'independent_live_approver_required')
  second=str(payload.get('second_approval_code',''))
  if not settings.second_operator_approval_code or not hmac.compare_digest(second,settings.second_operator_approval_code):raise safe_http(403,'invalid_second_live_approval')
  approval={**existing,'active':True,'pending_second_approval':False,'approvers':(existing.get('approvers') or [])+[actor],'expires_at':expires}
 else:
  code=str(payload.get('approval_code',''))
  if not settings.operator_approval_code or not hmac.compare_digest(code,settings.operator_approval_code):raise safe_http(403,'invalid_live_approval')
  if dual:
   approval={'active':False,'pending_second_approval':True,'approved_at':now.isoformat().replace('+00:00','Z'),'expires_at':expires,'scope':'live_mode','max_capital':settings.max_capital,'max_order_size':settings.max_order_size,'config_fingerprint':fingerprint,'required_approvers':2,'approvers':[actor]}
   memory.put('HOT','live_approval',approval);memory.event('operator_approval',{'status':'pending_second_approval','actor':actor});return {'status':'pending_second_approval','expires_at':expires,'required_approvers':2}
  approval={'active':True,'pending_second_approval':False,'approved_at':now.isoformat().replace('+00:00','Z'),'expires_at':expires,'scope':'live_mode','max_capital':settings.max_capital,'max_order_size':settings.max_order_size,'config_fingerprint':fingerprint,'required_approvers':1,'approvers':[actor]}
 memory.put('HOT','live_approval',approval);memory.event('operator_approval',{'active':True,'actor':actor,'approvers':approval['approvers'],'required_approvers':approval['required_approvers']});return {'status':'approved','expires_at':approval['expires_at'],'required_approvers':approval['required_approvers'],'note':'This records time-limited approval; live trading remains disabled unless LIVE_TRADING_ENABLED=true.'}
@app.post('/operator/revoke-live')
def revoke_live(_=Depends(require_operator)):
 memory.put('HOT','live_approval',{'active':False,'revoked_at':now_iso()});live_execution.kill_switch.activate('live approval revoked; reconcile outstanding venue orders','operator');h=memory.hot();h.mode=Mode.PAPER;memory.save_hot(h);memory.event('operator_approval',{'active':False,'reason':'revoked'});return {'status':'revoked','mode':'paper','reconciliation_required':True}
@app.post('/operator/rotate-key')
def rotate_key(scope:str='trade',_=Depends(require_admin)):
 if scope not in ('read','trade'):raise HTTPException(422,'scope must be read or trade')
 return security.rotate(scope)
@app.post('/operator/revoke-key/{key_id}')
def revoke_key(key_id:str,_=Depends(require_admin)):
 if not security.revoke(key_id):raise HTTPException(404,'Key not found')
 return {'status':'revoked','key_id':key_id}
@app.get('/operator/config')
def operator_config(principal=Depends(require_admin)):
 return runtime_config.payload()
@app.patch('/operator/config')
def update_operator_config(payload:dict,principal=Depends(require_admin)):
 try:
  result=runtime_config.update(payload.get('changes'),getattr(principal,'key_id','admin'),str(payload.get('reason') or 'operator update'))
 except ValueError as exc: raise HTTPException(422,str(exc))
 runtime_config.sync_process(result['values']);settings.max_portfolio_heat=float(result['values'].get('MAX_PORTFOLIO_HEAT',settings.max_portfolio_heat));strategies.items['relative_microstructure'].enabled=bool(result['values'].get('EXPERIMENTAL_STRATEGY_ENABLED',False))
 memory.event('runtime_config_changed',{'version':result['version'],'changes':list((payload.get('changes') or {}).keys()),'actor':getattr(principal,'key_id','admin'),'reason':str(payload.get('reason') or 'operator update')})
 return result
@app.post('/operator/config/rollback')
def rollback_operator_config(payload:dict,principal=Depends(require_admin)):
 try: result=runtime_config.rollback(int(payload.get('version')),getattr(principal,'key_id','admin'))
 except (TypeError,ValueError) as exc: raise HTTPException(422,str(exc))
 runtime_config.sync_process(result['values']);settings.max_portfolio_heat=float(result['values'].get('MAX_PORTFOLIO_HEAT',settings.max_portfolio_heat));strategies.items['relative_microstructure'].enabled=bool(result['values'].get('EXPERIMENTAL_STRATEGY_ENABLED',False))
 memory.event('runtime_config_changed',{'version':result['version'],'rollback_from':payload.get('version'),'actor':getattr(principal,'key_id','admin')})
 return result
def _paper_exploration_size(market, estimate):
 # Exploration is deliberately not a prediction. It samples the cheapest
 # executable side with a tiny, fixed contract amount so that fills and
 # resolutions can be observed without pretending that the market baseline
 # supplied an edge.
 if market.liquidity<1000 or (market.volume_known and market.volume_24h<5000):return 0,['exploration_liquidity_gate']
 if market.market_status!='active' or estimate.executable_price<=0:return 0,['exploration_market_gate']
 configured=min(.01,max(0.0001,float(os.getenv('AUTO_PAPER_EXPLORATION_SIZE','.01'))))
 cost=max(1e-9,float(estimate.executable_price))
 remaining=max(0.0,float(settings.max_portfolio_heat)-float(memory.hot().portfolio_heat))
 size=min(configured,remaining/cost)
 return max(0.0,size),['paper_exploration_fixed_size','research_evidence_excluded'] if size>0 else ['portfolio_heat_gate']

def _decide_impl(req:DecisionRequest):
 h=memory.hot();strategy=strategies.get(req.strategy_id)
 if not strategy:raise HTTPException(422,f'Unknown strategy: {req.strategy_id}')
 if not strategy.enabled:raise HTTPException(403,f'Strategy disabled: {req.strategy_id}')
 if req.exploration and (h.mode!=Mode.PAPER or req.strategy_id!='paper_exploration'):raise HTTPException(422,'Exploration is only available through the paper_exploration strategy in paper mode.')
 history=[1 if d.resolved_yes else 0 for d in memory.decisions() if d.market_type==req.market.market_type and d.resolved_yes is not None]
 if req.strategy_id=='relative_microstructure':
  experimental=experimental_estimate(req.market)
  if experimental is None:raise HTTPException(422,'Experimental strategy requires calibrated model evidence and both contract books.')
  req.market.reference_rate=experimental['probability'];req.market.model_version=experimental['model_version'];req.market.model_lower_bound=experimental['lower_bound'];req.market.model_upper_bound=experimental['upper_bound'];req.market.model_uncertainty=experimental['uncertainty'];req.market.signals={'relative_microstructure':experimental['probability']}
 attention_plan=plan_attention(req.market,strategy_id=req.strategy_id,hot_state=h,max_portfolio_heat=settings.max_portfolio_heat)
 opportunity_status={'analyze':'analyzed','request_information':'rejected_evidence','defer':'delayed','skip':'skipped'}.get(attention_plan.disposition,'discovered')
 memory.save_opportunity(OpportunityObservation(opportunity_id='opportunity_'+attention_plan.plan_id,market_id=req.market.market_id,strategy_id=req.strategy_id,status=opportunity_status,reason='; '.join(attention_plan.reasons),snapshot_hash=req.market.snapshot_hash,payload={'attention_plan':attention_plan.model_dump(mode='json')}))
 telemetry.set('vesper_attention_score',attention_plan.attention_score,labels={'strategy':req.strategy_id});telemetry.inc('vesper_attention_plans_total',labels={'disposition':attention_plan.disposition,'strategy':req.strategy_id})
 for request in attention_plan.information_requests: telemetry.inc('vesper_information_requests_total',labels={'type':request.request_type,'required':str(request.required).lower()})
 has_reference_evidence=req.market.reference_rate is not None or bool(req.market.signals) or bool(history)
 calibrated=reference.calibrated_prior(req.market.market_type,req.market.reference_rate if req.market.reference_rate is not None else req.market.price,history)
 objective_policy=ObjectivePolicy.model_validate(memory.get('REFERENCE','objective_policy') or default_objective_policy().model_dump())
 e=edge.estimate(req.market,calibrated);action_evaluations=evaluate_actions(req.market,fair_probability=e.fair_probability,confidence=e.confidence,uncertainty=e.uncertainty,hot_state=h,objective_policy=objective_policy);policy_proposal=propose_action(action_evaluations,e.recommended_side);telemetry.inc('vesper_consequence_evaluations_total',labels={'preferred_side':e.recommended_side,'policy_status':policy_proposal.status});trust=memory.effective_trust(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime)
 memory_candidates=memory.all('semantic')+memory.all('procedural')+memory.all('failure')+memory.all('model')
 retrieved_memories=rank_memories(memory_candidates,f'{req.market.question} {req.market.regime} {req.strategy_id}',limit=8)
 telemetry.inc('vesper_memory_retrievals_total',value=1,labels={'count':str(len(retrieved_memories))})
 size,gates=_paper_exploration_size(req.market,e) if req.exploration else risk.size(e,req.market,trust,strategy,settings.max_portfolio_heat);size*=memory.scar_size_multiplier(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime)
 quality_gates=[]
 quote_time=req.market.quote_observed_at or req.market.observed_at
 if quote_time is not None and (datetime.now(timezone.utc)-quote_time).total_seconds()>120:quality_gates.append('stale_market_input')
 if req.market.quality_score<.95:quality_gates.append('market_quality_below_threshold')
 if req.market.quote_skew_seconds>max(1,float(os.getenv('MAX_CONTRACT_QUOTE_SKEW_SECONDS','10'))):quality_gates.append('incoherent_contract_books')
 if req.market.market_status!='active':quality_gates.append('market_not_active')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and req.market.source.startswith('polymarket') and not ingestion_store.verified_input_matches(req.market):quality_gates.append('verified_market_snapshot_required')
 if req.market.source.startswith('polymarket') and (req.market.yes_ask is None or req.market.no_ask is None):quality_gates.append('both_contract_quotes_required')
 if req.market.source.startswith('polymarket') and (not req.market.yes_book_asks or not req.market.no_book_asks):quality_gates.append('both_contract_books_required')
 if attention_plan.disposition=='request_information' and req.market.source.startswith('polymarket'):quality_gates.append('attention_information_required')
 if not fast_market_allowed(req.market.resolution_hours,req.market.source):quality_gates.append('slow_market_excluded')
 if not has_reference_evidence and req.market.source.startswith('polymarket'):quality_gates.append('reference_evidence_required')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and req.market.source=='manual':quality_gates.append('untrusted_market_source')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and (req.market.yes_ask is None or req.market.no_ask is None):quality_gates.append('executable_quote_required')
 if quality_gates:size=0;gates+=quality_gates
 flow=toxic.inspect(req.market,req.flow_imbalance,req.large_wallet_signal);size,risk_reasons=portfolio.gate(req.market,size,req.flow_imbalance,req.large_wallet_signal,e.recommended_side);gates+=risk_reasons+flow['flags'];relevant=memory.active_scars(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime);principles=[p for p in memory.principles() if p.status=='active' and p.strategy_id in (req.strategy_id,'global')];cited=[s.id for s in relevant];cp=[p.id for p in principles]
 preferred_evaluation=next((item for item in action_evaluations if item.action==f'BUY {e.recommended_side}'),None)
 if not req.exploration and policy_proposal.status=='rejected' and preferred_evaluation is not None and preferred_evaluation.available and size>0:size=0;gates+=['consequence_policy_rejected']
 if bucket_killer.suspended(req.strategy_id,req.market.market_type,req.market.regime):size=0;gates+=['bucket_suspended_negative_expectancy']
 if any(s.impact.constitutional and s.impact.max_size_multiplier<=0 for s in relevant):size=0;gates+=['scar_constitutional_stop']
 if not req.evidence_complete:size=0;gates+=['evidence_completeness_gate']
 if not flow['toxic'] and not any(x in gates for x in ['daily_kill_switch','weekly_kill_switch']):size*=flow['size_multiplier']
 if h.mode==Mode.LIVE:
  if not live_approval_active():size=0;gates+=['live_approval_expired']
  if not live_execution_reconciled():size=0;gates+=['live_execution_reconciliation_gate']
  if os.getenv('LIVE_TRADING_ENABLED','false').lower()!='true':size=0;gates+=['live_operator_gate_disabled']
  if len(history)<settings.min_sample:size=0;gates+=['live_sample_gate']
  evidence=live_evidence_checks()
  if not all(evidence.values()):size=0;gates += [f'live_statistical_gate_{name}' for name,passed in evidence.items() if not passed]
  if settings.max_order_size<=0:size=0;gates+=['live_order_limit_gate']
  else:size=min(size,settings.max_order_size)
 fill_profile=paper_execution_profile(req.market,size,e.recommended_side) if h.mode==Mode.PAPER and size>0 else {'fill_fraction':1.0,'execution_price':None,'reason':'non_paper_or_zero_size'}
 fill_fraction=fill_profile['fill_fraction'];paper_execution_price=fill_profile['execution_price']
 if h.mode==Mode.PAPER and size>0 and fill_fraction<=0:
  size=0;paper_execution_price=None;fill_profile['reason']='paper_no_fill';gates.append('paper_no_fill')
 if h.mode==Mode.PAPER and size>0 and paper_execution_price is not None and paper_execution_price>=e.side_probability and not req.exploration:
  size=0;fill_fraction=0.0;paper_execution_price=None;fill_profile['reason']='paper_depth_erased_edge';gates.append('paper_depth_erased_edge')
 action='DO NOTHING' if size<=0 else 'BUY';risk_score=min(10,max(1,int(e.raw_edge*100+(10 if relevant else 3))));rationale=('No trade: '+'; '.join(gates)) if size<=0 else ('Exploration sample: fixed-size executable quote selection; excluded from live-readiness evidence.' if req.exploration else 'Calibrated probability, executable side edge, liquidity, capacity, trust, scars, and portfolio gates passed.');status='paper' if h.mode==Mode.PAPER else 'shadow' if h.mode==Mode.SHADOW else 'live-gated';fill_reason=fill_profile['reason']
 paper_reference_price=req.market.price if e.recommended_side=='YES' else 1-req.market.price
 execution_price=paper_execution_price if paper_execution_price is not None else e.executable_price
 paper_cost=max(0.0,(execution_price-paper_reference_price)*size*fill_fraction) if h.mode==Mode.PAPER else 0.0
 paper_ev=(e.side_probability-execution_price)*size*fill_fraction if h.mode==Mode.PAPER else e.raw_edge*size*fill_fraction
 d=DecisionRecord(id='decision_'+os.urandom(5).hex(),mode=h.mode,market_id=req.market.market_id,strategy_id=req.strategy_id,market_type=req.market.market_type,regime=req.market.regime,action=action,side=e.recommended_side if size else None,size=size,price=req.market.price,fair_probability=e.fair_probability,confidence=e.confidence,risk_score=risk_score,edge=e.raw_edge,executable_price=e.executable_price,expected_value=paper_ev,rationale=rationale,cited_scars=cited,cited_principles=cp,gates=gates,status=status,source=req.market.source,model_version='paper_exploration_v1' if req.exploration else req.market.model_version,model_provenance=({'provider':'paper_exploration','version':'paper_exploration_v1','selection':'lowest executable ask','underlying_model':req.market.model_version} if req.exploration else req.market.model_provenance),raw_model_probability=req.market.raw_model_probability,model_probability=req.market.model_probability,quality_score=req.market.quality_score,snapshot_hash=req.market.snapshot_hash,observed_at=req.market.observed_at.isoformat() if req.market.observed_at else None,quote_observed_at=req.market.quote_observed_at.isoformat() if req.market.quote_observed_at else None,book_sequence=req.market.book_sequence,fill_model_version='paper_microstructure_v1' if h.mode==Mode.PAPER else None,model_lower_bound=req.market.model_lower_bound,model_upper_bound=req.market.model_upper_bound,model_uncertainty=req.market.model_uncertainty,model_calibration_samples=req.market.model_calibration_samples,model_calibration_status=req.market.model_calibration_status,paper_fill_fraction=fill_fraction,paper_execution_price=paper_execution_price,paper_cost=paper_cost,paper_fill_reason=fill_reason,research_eligible=(not req.exploration) and req.market.source.startswith('polymarket') and bool(req.market.snapshot_hash) and req.market.quote_observed_at is not None,market_context={'resolution_hours':req.market.resolution_hours,'market_end_time':req.market.market_end_time.isoformat() if req.market.market_end_time else None,'yes_bid':req.market.yes_bid,'yes_ask':req.market.yes_ask,'no_bid':req.market.no_bid,'no_ask':req.market.no_ask,'liquidity':req.market.liquidity,'volume_24h':req.market.volume_24h,'fee_rate':req.market.fee_rate,'slippage_bps':req.market.slippage_bps,'correlation_cluster':correlation_cluster(req.market),'event_family':event_family(req.market.question,req.market.market_type),'paper_exploration':req.exploration,'evidence_excluded_reason':'separate exploration strategy' if req.exploration else None})
 d.market_context['retrieved_memories']=retrieved_memories
 d.market_context.update({'yes_token_id':req.market.yes_token_id,'no_token_id':req.market.no_token_id,'yes_quote_observed_at':req.market.yes_quote_observed_at.isoformat() if req.market.yes_quote_observed_at else None,'no_quote_observed_at':req.market.no_quote_observed_at.isoformat() if req.market.no_quote_observed_at else None,'quote_skew_seconds':req.market.quote_skew_seconds,'yes_ask_levels':[level.model_dump() for level in req.market.yes_book_asks],'no_ask_levels':[level.model_dump() for level in req.market.no_book_asks]})
 d.market_context.update(canonical_dependency_metadata(req.market.question,req.market.market_type,market_id=req.market.market_id,resolution_end=req.market.market_end_time,source=req.market.source))
 d.market_context.update({'attention_plan':attention_plan.model_dump(mode='json'),'information_requests':[item.model_dump(mode='json') for item in attention_plan.information_requests],'action_evaluations':[item.model_dump(mode='json') for item in action_evaluations],'policy_proposal':policy_proposal.model_dump(mode='json')})
 episode_id='episode_'+os.urandom(8).hex()
 d.episode_id=episode_id;d.objective_policy_version=OBJECTIVE_POLICY_VERSION;d.policy_version=POLICY_VERSION;d.risk_policy_version=RISK_POLICY_VERSION;d.data_version=DATA_VERSION
 final_opportunity_status='selected_for_paper' if d.size>0 and h.mode==Mode.PAPER else 'selected_for_shadow' if d.size>0 and h.mode==Mode.SHADOW else 'rejected_risk' if gates else 'rejected'
 memory.save_opportunity(OpportunityObservation(opportunity_id='opportunity_'+attention_plan.plan_id,market_id=req.market.market_id,strategy_id=req.strategy_id,status=final_opportunity_status,reason='; '.join(gates) or rationale,snapshot_hash=req.market.snapshot_hash,episode_id=episode_id,payload={'decision_id':d.id,'gates':gates,'attention_plan':attention_plan.model_dump(mode='json')}))
 observation_event=make_canonical_event('decision_engine','market_input_observed',req.market.model_dump(mode='json'),event_time=req.market.observed_at or d.created_at,episode_id=episode_id,source_event_id=req.market.snapshot_hash,quality=EventQuality(completeness=1.0 if req.market.snapshot_hash else .75,confidence=req.market.quality_score,missing_fields=(['snapshot_hash'] if not req.market.snapshot_hash else []),valid=True))
 belief_state=build_belief_state(req.market,source_event_id=observation_event.event_id,hot_state=h)
 d.belief_state_id=belief_state.belief_state_id
 episode=build_episode(d,req.market,belief_state,objective_policy,attention_plan=attention_plan,action_evaluations=action_evaluations,policy_proposal=policy_proposal)
 episode.memory_links={'retrieved_memories':retrieved_memories}
 decision_event=make_canonical_event('decision_engine','decision_evaluated',{'decision':d.model_dump(mode='json'),'episode_id':episode_id},event_time=d.created_at,episode_id=episode_id,source_event_id=d.id,quality=EventQuality(confidence=d.confidence,valid=True))
 # Live capital is reserved by the execution service. Portfolio heat reflects
 # durable fills, never the pre-submit decision size.
 effective_exposure=exposure_notional(d.size*d.paper_fill_fraction,paper_execution_price if paper_execution_price is not None else execution_price,req.market.fee_rate,req.market.slippage_bps) if h.mode!=Mode.LIVE else 0.0
 if effective_exposure>0:
  h.portfolio_heat+=effective_exposure;h.open_risk+=effective_exposure
 telemetry.inc('vesper_decisions_total',labels={'mode':h.mode.value,'action':action,'strategy':req.strategy_id});telemetry.set('vesper_portfolio_heat',h.portfolio_heat)
 memory.save_decision(d,h if effective_exposure>0 else None,episode=episode,eda_events=[observation_event,decision_event]);memory.event('decision',d.model_dump())
 if req.execute and d.size>0:
  external_submission=False
  try:
   if h.mode==Mode.LIVE:
    if not d.source.startswith('polymarket'):raise RuntimeError('Live orders require a verified Polymarket market source.')
    current_market=markets.market(d.market_id);yes_token,no_token=markets.token_pair(current_market)
    if yes_token!=req.market.yes_token_id or no_token!=req.market.no_token_id:raise RuntimeError('Live token identity changed during revalidation.')
    yes_book=markets.book(yes_token);no_book=markets.book(no_token)
    if current_market.get('closed') or not current_market.get('active',True) or not yes_book.best_ask or not no_book.best_ask:raise RuntimeError('Market is no longer active or executable.')
    selected_book=yes_book if d.side=='YES' else no_book
    quote_age=(datetime.now(timezone.utc)-datetime.fromisoformat(selected_book.observed_at.replace('Z','+00:00'))).total_seconds()
    if quote_age>max(1,float(os.getenv('LIVE_MAX_QUOTE_AGE_SECONDS','3'))):raise RuntimeError('Live quote is stale at submission time.')
    current_ask=yes_book.best_ask if d.side=='YES' else no_book.best_ask
    previous_ask=req.market.yes_ask if d.side=='YES' else req.market.no_ask
    max_drift=max(0,float(os.getenv('LIVE_MAX_PRICE_DRIFT_BPS','50')))/10000
    if previous_ask is None or current_ask>previous_ask*(1+max_drift):raise RuntimeError('Live executable price moved beyond tolerance.')
    available_depth=sum(level.size for level in selected_book.asks if level.price<=current_ask*(1+max_drift))
    if available_depth+1e-12<d.size:raise RuntimeError('Current executable depth cannot support the decision size.')
    d.executable_price=current_ask
   raw_result=adapter_for(h.mode,live_execution).execute(d)
   if h.mode==Mode.LIVE: external_submission=True
   result=validate_execution_result(raw_result,d);order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id=result['client_order_id'],decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price,status=OrderStatus(result['status']),filled_size=result.get('filled_size',0),filled_notional=result.get('filled_notional',0),filled_fees=result.get('filled_fees',0),average_fill_price=result.get('average_fill_price'),venue_order_id=result.get('venue_order_id'),error=result.get('error'))
   if h.mode==Mode.LIVE and result.get('fills'):
    live_execution._ingest_fills(order,{'fills':result['fills'],'average_fill_price':result.get('average_fill_price')});totals=live_execution._fill_totals(order);order.filled_size=float(totals['quantity'] or 0);order.filled_notional=float(totals['notional'] or 0);order.filled_fees=float(totals['fees'] or 0);order.average_fill_price=order.filled_notional/order.filled_size if order.filled_size else None
   d.order_id=order.id;d.executed_size=order.filled_size;d.executed_notional=order.filled_notional;d.executed_fees=order.filled_fees;d.executed_average_price=order.average_fill_price;d.execution_reconciled=h.mode!=Mode.LIVE and order.status in {OrderStatus.FILLED,OrderStatus.CANCELED,OrderStatus.EXPIRED,OrderStatus.REJECTED,OrderStatus.FAILED}
   if h.mode==Mode.LIVE and order.filled_size>0:
    h.portfolio_heat+=order.filled_notional+order.filled_fees;h.open_risk+=order.filled_notional+order.filled_fees
   memory.save_order(order,d);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});memory.event('execution',order.model_dump());memory.append_eda_event(make_canonical_event('execution','execution_result',order.model_dump(mode='json'),episode_id=d.episode_id,source_event_id=order.id,quality=EventQuality(confidence=1.0 if d.execution_reconciled else .5,valid=True)))
  except Exception as exc:
   if h.mode==Mode.LIVE and external_submission:
    live_execution.kill_switch.activate('live submission persistence failed; reconciliation required', 'system')
    d.status='reconciliation-required';d.outcome='pending';d.execution_reconciled=False;d.rationale=f'Live submission was acknowledged but local persistence failed: {exc}'
    memory.save_decision(d);telemetry.error('live_persistence_failure');memory.event('execution_reconciliation_required',{'decision_id':d.id,'error':str(exc)});memory.append_eda_event(make_canonical_event('execution','execution_reconciliation_required',{'decision_id':d.id,'error':str(exc)},episode_id=d.episode_id,source_event_id=d.id,quality=EventQuality(confidence=0.0,valid=False)))
    return d
   # An adapter failure is terminal for this order attempt. Release the
   # reservation immediately so a transient venue/configuration error cannot
   # strand portfolio heat until a later settlement job runs.
   h.portfolio_heat=max(0,h.portfolio_heat-effective_exposure);h.open_risk=max(0,h.open_risk-effective_exposure)
   d.outcome='execution_failed';d.status='execution-failed';d.resolved_at=now_iso();d.rationale=f'Execution failed: {exc}'
   memory.save_decision(d,h)
   order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id='failed_'+os.urandom(6).hex(),decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price,status=OrderStatus.FAILED,error=str(exc));d.order_id=order.id;memory.save_order(order,d);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});telemetry.error('order_execution');memory.event('execution_blocked',order.model_dump());memory.append_eda_event(make_canonical_event('execution','execution_failed',order.model_dump(mode='json'),episode_id=d.episode_id,source_event_id=order.id,quality=EventQuality(confidence=0.0,valid=False)))
 return d
@app.post('/decide',response_model=DecisionRecord)
def decide(req:DecisionRequest,_=Depends(require_trade)):
 with memory.portfolio_lock():
  return _decide_impl(req)

@app.post('/outcomes',response_model=DecisionRecord)
def outcome(req:OutcomeRequest,_=Depends(require_settlement_admin)):
 if req.outcome not in ('win','loss','push','void','failure','negative'):raise HTTPException(422,'Unsupported terminal outcome')
 if abs(float(req.pnl))>1e-12:raise HTTPException(422,'Client-supplied PnL is forbidden; settlement must be derived from execution and resolution records')
 with memory.decision_lock(req.decision_id):
  d=next((x for x in memory.decisions() if x.id==req.decision_id),None)
  if not d:raise HTTPException(404,'Decision not found')
  if d.outcome!='pending':raise HTTPException(409,'Decision already has a terminal outcome')
  if d.mode==Mode.LIVE:
   if not d.execution_reconciled:raise HTTPException(409,'Live execution is unresolved; reconcile all fills before settlement')
   if d.executed_size<=0:raise HTTPException(409,'Cannot settle a live decision with no filled exposure')
  elif d.size<=0 or d.paper_fill_fraction<=0:raise HTTPException(409,'Cannot settle a decision with no filled exposure')
  settled_pnl=0.0;resolved_yes=req.resolved_yes
  if resolved_yes is not None and d.side in ('YES','NO'):
   won=req.resolved_yes==(d.side=='YES')
   if req.outcome in ('win','loss') and ((req.outcome=='win')!=won):raise HTTPException(422,'Outcome conflicts with resolved market result')
   if req.outcome in ('win','loss'):
    effective_size=d.executed_size if d.execution_reconciled else d.size*d.paper_fill_fraction
    if d.execution_reconciled and (d.executed_average_price is None or d.executed_size<=0):raise HTTPException(409,'Execution is terminal but immutable fill price is missing')
    settlement_price=d.executed_average_price if d.execution_reconciled and d.executed_average_price is not None else d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price
    settled_pnl=(effective_size*(1-settlement_price) if won else -effective_size*settlement_price)-float(d.executed_fees if d.execution_reconciled else 0)
  elif req.outcome in ('win','loss') and d.side in ('YES','NO'):
   if req.close_price is None or req.close_price not in (0,1):raise HTTPException(422,'win/loss settlement requires resolved_yes or terminal close_price 0/1')
   resolved_yes=req.close_price==1
   won=resolved_yes==(d.side=='YES')
   if (req.outcome=='win')!=won:raise HTTPException(422,'Outcome conflicts with terminal close price')
   if d.execution_reconciled and (d.executed_average_price is None or d.executed_size<=0):raise HTTPException(409,'Execution is terminal but immutable fill price is missing')
   effective_size=d.executed_size if d.execution_reconciled else d.size*d.paper_fill_fraction;entry=d.executed_average_price if d.execution_reconciled and d.executed_average_price is not None else d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price;terminal_value=req.close_price if d.side=='YES' else 1-req.close_price;settled_pnl=effective_size*(terminal_value-entry)-float(d.executed_fees if d.execution_reconciled else 0)
  elif req.outcome in ('win','loss') and req.close_price is None:raise HTTPException(422,'win/loss settlement requires resolved_yes or terminal close_price 0/1')
  return settle_decision(memory,metrics_engine,scars,d,req.outcome,settled_pnl,req.clv,resolved_yes,req.evidence_complete,'operator',process_score=req.process_score)
@app.post('/demo/clear-learning')
def clear_learning(_=Depends(require_admin)):
 if settings.deployment_stage!='paper' or os.getenv('VESPER_ENV','development').lower() in {'production','prod'}:raise safe_http(404,'not_found')
 memory.delete_learning_memory();return {'message':'Learning memory removed; the agent returns to naive behavior.'}
@app.post('/demo/seed-market')
def seed_market(_=Depends(require_admin)):
 if settings.deployment_stage!='paper' or os.getenv('VESPER_ENV','development').lower() in {'production','prod'}:raise safe_http(404,'not_found')
 return {'market_id':'demo-market','message':'Use price 0.45, liquidity 25000, volume 100000, reference_rate 0.60.'}

