import os
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Cookie
from fastapi.responses import PlainTextResponse,JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .models import *
from .config import settings,validate_runtime_config
from .strategies import StrategyRegistry
from .secondary_signals import SecondarySignals
from .memory import TradingMemory
from .engines import EdgeEngine,RiskEngine,ScarEngine
from .metrics import MetricsEngine
from .market_data import PolymarketData
from .graph import ExperienceGraph
from .risk import PortfolioRisk
from .portfolio import ToxicFlowDetector, BucketKiller
from .quant import ReferenceClassEngine
from .adapters import adapter_for,paper_execution_profile
from .ingestion import IngestionStore
from .autonomy import AutonomyGate
from .observability import telemetry
from .security import SecurityManager
from .settlement import settle_decision
from .market_policy import fast_market_allowed,fast_markets_only,fast_max_resolution_hours
from datetime import datetime,timezone
import time,uuid,json,logging
validate_runtime_config()
app=FastAPI(title='Vesper Trading',version='6.0.0')
cors_origins=[item.strip().rstrip('/') for item in os.getenv('CORS_ORIGINS','http://localhost:5173').split(',') if item.strip()]
app.add_middleware(CORSMiddleware,allow_origins=cors_origins,allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Vesper-Key','X-Request-ID'],allow_credentials=True)
log=logging.getLogger('vesper.api')

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
   if request.cookies.get('vesper_session') and request.method in {'POST','PUT','PATCH','DELETE'}:
    origin=request.headers.get('origin')
    allowed={item.strip().rstrip('/') for item in os.getenv('CORS_ORIGINS','http://localhost:5173').split(',') if item.strip()}
    if origin and origin.rstrip('/') not in allowed:raise HTTPException(403,'Cross-origin state change blocked.')
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

@app.post('/auth/session')
def create_session(request:Request,x_vesper_key:str|None=Header(default=None)):
 token,payload=security.create_session(x_vesper_key)
 response=PlainTextResponse(json.dumps({'authenticated':True,'scope':payload['scope'],'expires_at':payload['exp']}),media_type='application/json')
 response.set_cookie('vesper_session',token,max_age=settings.session_ttl_seconds,httponly=True,secure=settings.cookie_secure or request.url.scheme=='https',samesite=settings.cookie_samesite,path='/')
 return response

@app.get('/auth/session')
def session_status(vesper_session:str|None=Cookie(default=None)):
 principal=security.authenticate_session(vesper_session,'read');return {'authenticated':True,'key_id':principal.key_id,'scope':principal.scope}

@app.delete('/auth/session')
def delete_session():
 response=PlainTextResponse('',status_code=204);response.delete_cookie('vesper_session',path='/');return response
memory=TradingMemory();security=SecurityManager(settings,memory.db);edge=EdgeEngine();risk=RiskEngine(memory);portfolio=PortfolioRisk(memory);toxic=ToxicFlowDetector();bucket_killer=BucketKiller(memory);scars=ScarEngine(memory);metrics_engine=MetricsEngine(memory);markets=PolymarketData();graph=ExperienceGraph(memory);reference=ReferenceClassEngine();strategies=StrategyRegistry();secondary=SecondarySignals();ingestion_store=IngestionStore();autonomy=AutonomyGate(memory,ingestion_store)
def live_evidence_checks():
 report=research_report(memory.decisions());oos=report.get('out_of_sample') or {};minimum=max(10,int(os.getenv('RESEARCH_MIN_OOS_BUCKETS','10')));min_expectancy=float(os.getenv('LIVE_MIN_OOS_EXPECTANCY','0'));min_brier_lift=float(os.getenv('LIVE_MIN_BRIER_LIFT','0'));min_log_loss_lift=float(os.getenv('LIVE_MIN_LOG_LOSS_LIFT','0'))
 return {'research_available':report.get('status')=='available','oos_sample':int(oos.get('count') or 0)>=minimum,'oos_expectancy':oos.get('expectancy_ci_low') is not None and float(oos['expectancy_ci_low'])>min_expectancy,'oos_brier_lift':oos.get('brier_lift_ci_low') is not None and float(oos['brier_lift_ci_low'])>min_brier_lift,'oos_log_loss_lift':oos.get('log_loss_lift_ci_low') is not None and float(oos['log_loss_lift_ci_low'])>min_log_loss_lift}
def live_execution_reconciled():
 return False
@app.get('/health')
def health():telemetry.set('vesper_mode',{'paper':0,'shadow':1,'live':2}.get(memory.hot().mode.value,0));return {'status':'ok','service':'vesper-trading','mode':memory.hot().mode,'memory_load_bearing':True,'database':'postgresql','live_enabled':settings.live_enabled}
def _readiness_payload():
 q=ingestion_store.quality();h=memory.hot();approval=memory.get('HOT','live_approval') or {}
 decisions=memory.decisions();sample_keys={f'{d.strategy_id}:{d.market_id}:{d.regime}:{d.model_version or "none"}' for d in decisions if d.size>0 and d.paper_fill_fraction>0 and d.outcome!='pending'}
 live_checks={'auth_configured':bool(settings.api_key and settings.admin_key),'limits_configured':settings.max_capital>0 and settings.max_order_size>0,'sample_gate':len(sample_keys)>=settings.min_sample,'data_quality':q['score']>=settings.min_data_quality and not q['stale'],'operator_approved':bool(approval.get('active')),'execution_reconciled':live_execution_reconciled(),**live_evidence_checks()}
 checks={'api':True,'memory':memory.db.ping(),'data_quality':q['score']>=settings.min_data_quality or h.mode==Mode.PAPER,'data_fresh':not q['stale'] or h.mode==Mode.PAPER,'live_safe':all(live_checks) if h.mode==Mode.LIVE else True};return {'ready':all(checks.values()),'checks':checks,'live_checks':live_checks,'quality':q}
@app.get('/ready')
def ready():
 payload=_readiness_payload()
 return payload if payload['ready'] else JSONResponse(payload,status_code=503)
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
@app.get('/graph')
def graph_edges(_=Depends(require_api_key)):return graph.edges()
@app.get('/audit')
def audit(limit:int=200,_=Depends(require_api_key)):return memory.audit(max(1,min(limit,1000)))
@app.get('/risk')
def risk_state(_=Depends(require_api_key)):return {'portfolio_heat':portfolio.heat(),'daily_pnl':memory.hot().daily_pnl,'weekly_pnl':memory.hot().weekly_pnl,'correlation_regime':memory.hot().correlation_regime}
@app.get('/dashboard')
def dashboard(_=Depends(require_api_key)):
 h=memory.hot();return {'mode':h.mode,'risk':risk_state(),'pipeline':autonomy.status(),'observations':ingestion_store.status(),'decisions':len(memory.decisions()),'scars':len(memory.scars()),'principles':len(memory.principles()),'metrics':len(memory.snapshots())}
@app.get('/markets')
def list_markets(limit:int=20):
 try:return markets.markets(limit)
 except Exception as e:return {'error':'market data unavailable','detail':str(e)}
@app.get('/markets/{market_id}')
def get_market(market_id):
 try:return markets.market(market_id)
 except Exception as e:raise HTTPException(502,str(e))
@app.get('/markets/input/{market_id}',response_model=MarketInput)
def market_input(market_id:str,_=Depends(require_api_key)):
 try:
  market=markets.market(market_id);yes_token,no_token=markets.token_pair(market)
  yes_book=markets.book(yes_token) if yes_token else None;no_book=markets.book(no_token) if no_token else None
  result=markets.to_input(market,yes_book=yes_book,no_book=no_book);ingestion_store.save_verified_input(result);return result
 except Exception as e:raise HTTPException(502,str(e))
@app.get('/markets/book/{token_id}')
def get_book(token_id):
 try:return markets.book(token_id)
 except Exception as e:raise HTTPException(502,str(e))
@app.get('/markets/quality/{market_id}',response_model=MarketQuality)
def market_quality(market_id:str,token_id:str|None=None,_=Depends(require_api_key)):
 try:
  market=markets.market(market_id);book=markets.book(token_id) if token_id else None;return markets.quality(market,book)
 except Exception as e:raise HTTPException(502,str(e))
@app.post('/signals')
def signals(payload:dict,_=Depends(require_trade)):return secondary.analyze(payload.get('question',''),payload.get('context',{}))
@app.get('/operations')
def operations(_=Depends(require_api_key)):return {'mode':memory.hot().mode,'live_enabled':settings.live_enabled,'max_capital':settings.max_capital,'max_order_size':settings.max_order_size,'database':'postgresql','kill_switch':memory.hot().daily_pnl<=-.1 or memory.hot().weekly_pnl<=-.2}
@app.get('/pipeline/status')
def pipeline_status(_=Depends(require_api_key)):return autonomy.status()
@app.get('/pipeline/observations')
def pipeline_observations(_=Depends(require_api_key)):return ingestion_store.status()
@app.get('/readiness')
def readiness(_=Depends(require_api_key)):return _readiness_payload()
@app.get('/readiness/summary')
def readiness_summary(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();decisions=memory.decisions();exposed=[d for d in decisions if d.size>0 and d.paper_fill_fraction>0];resolved=[d for d in exposed if d.outcome!='pending'];pending=[d for d in exposed if d.outcome=='pending'];key=lambda d:f'{d.strategy_id}:{d.market_id}:{d.regime}:{d.model_version or "none"}';resolved_keys={key(d) for d in resolved};pending_keys={key(d) for d in pending};snapshots=memory.snapshots();resolved_count=len(resolved);wins=sum(1 for d in resolved if d.outcome=='win');pnl=sum(float(d.pnl) for d in resolved);minimum=settings.min_sample;blockers=[]
 if len(resolved_keys)<minimum:blockers.append(f'Need {minimum-len(resolved_keys)} more independent resolved paper outcomes before the live sample gate can pass.')
 if q['score']<settings.min_data_quality or q['stale']:blockers.append('Market data must remain fresh and above the configured quality threshold.')
 if not settings.live_enabled:blockers.append('LIVE_TRADING_ENABLED is false.')
 if settings.max_capital<=0 or settings.max_order_size<=0:blockers.append('Live capital and order limits are not configured.')
 if not (memory.get('HOT','live_approval') or {}).get('active'):blockers.append('Explicit operator live approval has not been granted.')
 for evidence,passed in live_evidence_checks().items():
  if not passed:blockers.append(f'Live statistical gate has not passed: {evidence}.')
 blockers.append('Authenticated Polymarket CLOB execution and order reconciliation are not production-enabled.')
 learning='collecting' if not exposed else 'learning' if len(resolved_keys)<minimum else 'evidence_ready'
 return {'status':'paper_learning','learning_status':learning,'summary':f'{len(exposed)} exposed paper decisions across {len({key(d) for d in exposed})} independent market-strategy buckets; {len(resolved_keys)} resolved; {len(pending_keys)} awaiting settlement.','automation':{'enabled':os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true','decisions_per_tick':max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3'))),'cooldown_seconds':max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600'))),'min_resolution_hours':max(.01,float(os.getenv('AUTO_PAPER_MIN_RESOLUTION_HOURS','.05'))),'max_resolution_hours':min(max(.01,float(os.getenv('AUTO_PAPER_MAX_RESOLUTION_HOURS','24'))),max(.01,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')))),'fast_markets_only':os.getenv('FAST_MARKETS_ONLY','true').lower()=='true','fast_max_resolution_hours':max(.01,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1'))),'prefer_fast_markets':True},'paper':{'decisions':len(decisions),'exposed':len(exposed),'independent_buckets':len({key(d) for d in exposed}),'resolved':resolved_count,'independent_resolved':len(resolved_keys),'pending':len(pending),'independent_pending':len(pending_keys),'wins':wins,'win_rate':wins/resolved_count if resolved_count else None,'pnl':pnl,'metrics_buckets':len(snapshots),'minimum_sample':minimum},'research':research_report(decisions),'data':{'snapshots':q['snapshots'],'minimum_snapshots':int(os.getenv('MIN_MARKET_SNAPSHOTS','1000')),'quality':q['score'],'book_coverage':q.get('book_coverage',0),'stale':q['stale']},'worker':{'status':worker.get('status'),'last_resolved':worker.get('last_resolved',0),'last_pending':worker.get('last_pending',0)},'live':{'eligible':False,'blockers':blockers}}

def _research_probability(decision):
 value=decision.model_probability if decision.model_version and decision.model_probability is not None else decision.fair_probability
 return max(.0001,min(.9999,float(value)))

def _research_slice(items):
 if not items:return {'count':0,'wins':0,'win_rate':None,'pnl':0.0,'expectancy':None,'expectancy_ci_low':None,'expectancy_ci_high':None,'profit_factor':None,'max_drawdown':0.0,'avg_clv':None,'brier':None,'log_loss':None,'market_brier':None,'market_log_loss':None,'brier_lift_vs_market':None,'brier_lift_ci_low':None,'brier_lift_ci_high':None,'log_loss_lift_vs_market':None,'log_loss_lift_ci_low':None,'log_loss_lift_ci_high':None,'calibration_error':None,'avg_edge':None,'cost_drag':0.0,'unique_markets':0}
 wins=sum(1 for d in items if d.outcome=='win');pnl=sum(float(d.pnl) for d in items);profits=sum(max(0,float(d.pnl)) for d in items);losses=sum(min(0,float(d.pnl)) for d in items);equity=peak=drawdown=0.0
 for d in items:
  equity+=float(d.pnl);peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
 scored=[d for d in items if d.resolved_yes is not None];brier=sum((_research_probability(d)-(1.0 if d.resolved_yes else 0.0))**2 for d in scored)/len(scored) if scored else None
 log_loss=sum(-(1.0 if d.resolved_yes else 0.0)*math.log(_research_probability(d))-(0.0 if d.resolved_yes else 1.0)*math.log(1-_research_probability(d)) for d in scored)/len(scored) if scored else None
 market_scored=[d for d in scored if 0<d.price<1];market_brier=sum((d.price-(1.0 if d.resolved_yes else 0.0))**2 for d in market_scored)/len(market_scored) if market_scored else None
 market_log_loss=sum(-(1.0 if d.resolved_yes else 0.0)*math.log(max(.0001,min(.9999,d.price)))-(0.0 if d.resolved_yes else 1.0)*math.log(max(.0001,min(.9999,1-d.price))) for d in market_scored)/len(market_scored) if market_scored else None
 calibration_error=sum(abs(_research_probability(d)-(1.0 if d.resolved_yes else 0.0)) for d in scored)/len(scored) if scored else None
 def ci(values):
  if not values:return (None,None)
  mean=sum(values)/len(values)
  if len(values)<2:return (mean,mean)
  variance=sum((value-mean)**2 for value in values)/(len(values)-1);margin=float(os.getenv('RESEARCH_CONFIDENCE_Z','1.96'))*math.sqrt(variance/len(values));return (mean-margin,mean+margin)
 expectancy_low,expectancy_high=ci([float(d.pnl) for d in items])
 brier_lifts=[(d.price-(1.0 if d.resolved_yes else 0.0))**2-(_research_probability(d)-(1.0 if d.resolved_yes else 0.0))**2 for d in market_scored]
 log_lifts=[(-(1.0 if d.resolved_yes else 0.0)*math.log(max(.0001,min(.9999,d.price)))-(0.0 if d.resolved_yes else 1.0)*math.log(max(.0001,min(.9999,1-d.price))))-(-(1.0 if d.resolved_yes else 0.0)*math.log(_research_probability(d))-(0.0 if d.resolved_yes else 1.0)*math.log(1-_research_probability(d))) for d in market_scored]
 brier_low,brier_high=ci(brier_lifts);log_low,log_high=ci(log_lifts);clvs=[float(d.clv) for d in items if d.clv is not None]
 costs=sum(float(d.paper_cost) for d in items);return {'count':len(items),'wins':wins,'win_rate':wins/len(items),'pnl':pnl,'expectancy':pnl/len(items),'expectancy_ci_low':expectancy_low,'expectancy_ci_high':expectancy_high,'profit_factor':profits/abs(losses) if losses else None,'max_drawdown':drawdown,'avg_clv':sum(clvs)/len(clvs) if clvs else None,'brier':brier,'log_loss':log_loss,'market_brier':market_brier,'market_log_loss':market_log_loss,'brier_lift_vs_market':market_brier-brier if market_brier is not None and brier is not None else None,'brier_lift_ci_low':brier_low,'brier_lift_ci_high':brier_high,'log_loss_lift_vs_market':market_log_loss-log_loss if market_log_loss is not None and log_loss is not None else None,'log_loss_lift_ci_low':log_low,'log_loss_lift_ci_high':log_high,'calibration_error':calibration_error,'avg_edge':sum(float(d.edge) for d in items)/len(items),'cost_drag':costs,'unique_markets':len({d.market_id for d in items})}

def research_report(decisions):
 fast_only=fast_markets_only();fast_max=fast_max_resolution_hours()
 minimum_buckets=max(30,int(os.getenv('RESEARCH_MIN_INDEPENDENT_BUCKETS','30')))
 eligible=[d for d in decisions if d.size>0 and d.paper_fill_fraction>0 and d.outcome not in ('pending','void') and (not fast_only or float(d.market_context.get('resolution_hours',999999))<=fast_max)]
 versioned=[d for d in eligible if d.model_version]
 active_model_version=max(versioned,key=lambda d:d.created_at).model_version if versioned else None
 eligible=[d for d in eligible if d.model_version==active_model_version] if active_model_version else [d for d in eligible if not d.model_version]
 excluded_slow=sum(1 for d in decisions if d.size>0 and d.paper_fill_fraction>0 and d.outcome not in ('pending','void') and fast_only and float(d.market_context.get('resolution_hours',999999))>fast_max)
 raw=sorted(eligible,key=lambda d:d.created_at);groups={}
 for decision in raw:groups.setdefault(f'{decision.strategy_id}:{decision.market_id}:{decision.regime}:{decision.model_version or "none"}',[]).append(decision)
 resolved=[]
 for bucket in groups.values():
  first=bucket[0];pnl=sum(float(d.pnl) for d in bucket);clvs=[float(d.clv) for d in bucket if d.clv is not None];weights=[max(1e-9,float(d.size)*float(d.paper_fill_fraction)) for d in bucket]
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
 q=ingestion_store.quality();worker=ingestion_store.worker_health();snap=telemetry.snapshot();items=[]
 if q['stale']:items.append({'severity':'critical','code':'MARKET_DATA_STALE','message':'No fresh market observations within the freshness window.'})
 if q['score']<settings.min_data_quality:items.append({'severity':'warning','code':'MARKET_DATA_QUALITY_LOW','message':f"Market-data quality is {q['score']:.3f}."})
 if worker.get('stale'):items.append({'severity':'critical','code':'INGESTION_WORKER_STALE','message':'The ingestion worker has not reported a successful heartbeat recently.'})
 if snap['recent_errors_5m']>=5:items.append({'severity':'critical','code':'ERROR_BURST','message':f"{snap['recent_errors_5m']} errors observed in five minutes."})
 return {'active':items,'count':len(items),'generated_at':now_iso()}
@app.post('/mode/{mode}',response_model=HotState)
def set_mode(mode:Mode,_=Depends(require_admin)):
 h=memory.hot()
 if mode==Mode.LIVE:
  approval=memory.get('HOT','live_approval')
  if not settings.live_enabled or not approval or not approval.get('active'):raise HTTPException(403,'Live mode requires explicit operator approval and LIVE_TRADING_ENABLED=true.')
  if not live_execution_reconciled():raise HTTPException(403,'Live mode is unavailable until authenticated execution and venue reconciliation are production-enabled.')
  evidence=live_evidence_checks()
  if not all(evidence.values()):raise HTTPException(403,'Live mode requires positive out-of-sample evidence and improvement over the market baseline.')
 h.mode=mode;memory.save_hot(h);memory.event('mode_changed',{'mode':mode});return h
@app.post('/operator/request-live')
def request_live(payload:dict,_=Depends(require_admin)):
 code=str(payload.get('approval_code',''))
 if not settings.operator_approval_code or code!=settings.operator_approval_code:raise HTTPException(403,'Invalid operator approval.')
 approval={'active':True,'approved_at':now_iso(),'scope':'live_mode','max_capital':settings.max_capital,'max_order_size':settings.max_order_size};memory.put('HOT','live_approval',approval);memory.event('operator_approval',approval);return {'status':'approved','note':'This records approval; live trading remains disabled unless LIVE_TRADING_ENABLED=true.'}
@app.post('/operator/revoke-live')
def revoke_live(_=Depends(require_admin)):
 memory.put('HOT','live_approval',{'active':False,'revoked_at':now_iso()});h=memory.hot();h.mode=Mode.PAPER;memory.save_hot(h);memory.event('operator_approval',{'active':False,'reason':'revoked'});return {'status':'revoked','mode':'paper'}
@app.post('/operator/rotate-key')
def rotate_key(scope:str='trade',_=Depends(require_admin)):
 if scope not in ('read','trade'):raise HTTPException(422,'scope must be read or trade')
 return security.rotate(scope)
@app.post('/operator/revoke-key/{key_id}')
def revoke_key(key_id:str,_=Depends(require_admin)):
 if not security.revoke(key_id):raise HTTPException(404,'Key not found')
 return {'status':'revoked','key_id':key_id}
def _decide_impl(req:DecisionRequest):
 h=memory.hot();strategy=strategies.get(req.strategy_id)
 if not strategy:raise HTTPException(422,f'Unknown strategy: {req.strategy_id}')
 history=[1 if d.resolved_yes else 0 for d in memory.decisions() if d.market_type==req.market.market_type and d.resolved_yes is not None]
 has_reference_evidence=req.market.reference_rate is not None or bool(req.market.signals) or bool(history)
 calibrated=reference.calibrated_prior(req.market.market_type,req.market.reference_rate if req.market.reference_rate is not None else req.market.price,history)
 e=edge.estimate(req.market,calibrated);trust=memory.effective_trust(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime)
 size,gates=risk.size(e,req.market,trust,strategy,settings.max_portfolio_heat);size*=memory.scar_size_multiplier(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime)
 quality_gates=[]
 quote_time=req.market.quote_observed_at or req.market.observed_at
 if quote_time is not None and (datetime.now(timezone.utc)-quote_time).total_seconds()>120:quality_gates.append('stale_market_input')
 if req.market.quality_score<.95:quality_gates.append('market_quality_below_threshold')
 if req.market.quote_skew_seconds>max(1,float(os.getenv('MAX_CONTRACT_QUOTE_SKEW_SECONDS','10'))):quality_gates.append('incoherent_contract_books')
 if req.market.market_status!='active':quality_gates.append('market_not_active')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and req.market.source.startswith('polymarket') and not ingestion_store.verified_input_matches(req.market):quality_gates.append('verified_market_snapshot_required')
 if req.market.source.startswith('polymarket') and (req.market.yes_ask is None or req.market.no_ask is None):quality_gates.append('both_contract_quotes_required')
 if req.market.source.startswith('polymarket') and (not req.market.yes_book_asks or not req.market.no_book_asks):quality_gates.append('both_contract_books_required')
 if not fast_market_allowed(req.market.resolution_hours,req.market.source):quality_gates.append('slow_market_excluded')
 if not has_reference_evidence and req.market.source.startswith('polymarket'):quality_gates.append('reference_evidence_required')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and req.market.source=='manual':quality_gates.append('untrusted_market_source')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and (req.market.yes_ask is None or req.market.no_ask is None):quality_gates.append('executable_quote_required')
 if quality_gates:size=0;gates+=quality_gates
 flow=toxic.inspect(req.market,req.flow_imbalance,req.large_wallet_signal);size,risk_reasons=portfolio.gate(req.market,size,req.flow_imbalance,req.large_wallet_signal);gates+=risk_reasons+flow['flags'];relevant=memory.active_scars(req.strategy_id,req.market.market_type,req.market.market_id,req.market.regime);principles=[p for p in memory.principles() if p.status=='active' and p.strategy_id in (req.strategy_id,'global')];cited=[s.id for s in relevant];cp=[p.id for p in principles]
 if bucket_killer.suspended(req.strategy_id,req.market.market_type,req.market.regime):size=0;gates+=['bucket_suspended_negative_expectancy']
 if any(s.impact.constitutional and s.impact.max_size_multiplier<=0 for s in relevant):size=0;gates+=['scar_constitutional_stop']
 if not req.evidence_complete:size=0;gates+=['evidence_completeness_gate']
 if not flow['toxic'] and not any(x in gates for x in ['daily_kill_switch','weekly_kill_switch']):size*=flow['size_multiplier']
 if h.mode==Mode.LIVE:
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
 if h.mode==Mode.PAPER and size>0 and paper_execution_price is not None and paper_execution_price>=e.side_probability:
  size=0;fill_fraction=0.0;paper_execution_price=None;fill_profile['reason']='paper_depth_erased_edge';gates.append('paper_depth_erased_edge')
 action='DO NOTHING' if size<=0 else 'BUY';risk_score=min(10,max(1,int(e.raw_edge*100+(10 if relevant else 3))));rationale=('No trade: '+'; '.join(gates)) if size<=0 else 'Calibrated probability, executable side edge, liquidity, capacity, trust, scars, and portfolio gates passed.';status='paper' if h.mode==Mode.PAPER else 'shadow' if h.mode==Mode.SHADOW else 'live-gated';fill_reason=fill_profile['reason']
 paper_reference_price=req.market.price if e.recommended_side=='YES' else 1-req.market.price
 execution_price=paper_execution_price if paper_execution_price is not None else e.executable_price
 paper_cost=max(0.0,(execution_price-paper_reference_price)*size*fill_fraction) if h.mode==Mode.PAPER else 0.0
 paper_ev=(e.side_probability-execution_price)*size*fill_fraction if h.mode==Mode.PAPER else e.raw_edge*size*fill_fraction
 d=DecisionRecord(id='decision_'+os.urandom(5).hex(),mode=h.mode,market_id=req.market.market_id,strategy_id=req.strategy_id,market_type=req.market.market_type,regime=req.market.regime,action=action,side=e.recommended_side if size else None,size=size,price=req.market.price,fair_probability=e.fair_probability,confidence=e.confidence,risk_score=risk_score,edge=e.raw_edge,executable_price=e.executable_price,expected_value=paper_ev,rationale=rationale,cited_scars=cited,cited_principles=cp,gates=gates,status=status,source=req.market.source,model_version=req.market.model_version,raw_model_probability=req.market.raw_model_probability,model_probability=req.market.model_probability,quality_score=req.market.quality_score,snapshot_hash=req.market.snapshot_hash,observed_at=req.market.observed_at.isoformat() if req.market.observed_at else None,quote_observed_at=req.market.quote_observed_at.isoformat() if req.market.quote_observed_at else None,book_sequence=req.market.book_sequence,fill_model_version='paper_microstructure_v1' if h.mode==Mode.PAPER else None,model_lower_bound=req.market.model_lower_bound,model_upper_bound=req.market.model_upper_bound,model_uncertainty=req.market.model_uncertainty,model_calibration_samples=req.market.model_calibration_samples,model_calibration_status=req.market.model_calibration_status,paper_fill_fraction=fill_fraction,paper_execution_price=paper_execution_price,paper_cost=paper_cost,paper_fill_reason=fill_reason,market_context={'resolution_hours':req.market.resolution_hours,'market_end_time':req.market.market_end_time.isoformat() if req.market.market_end_time else None,'yes_bid':req.market.yes_bid,'yes_ask':req.market.yes_ask,'no_bid':req.market.no_bid,'no_ask':req.market.no_ask,'liquidity':req.market.liquidity,'volume_24h':req.market.volume_24h,'fee_rate':req.market.fee_rate,'slippage_bps':req.market.slippage_bps})
 d.market_context.update({'yes_quote_observed_at':req.market.yes_quote_observed_at.isoformat() if req.market.yes_quote_observed_at else None,'no_quote_observed_at':req.market.no_quote_observed_at.isoformat() if req.market.no_quote_observed_at else None,'quote_skew_seconds':req.market.quote_skew_seconds,'yes_ask_levels':[level.model_dump() for level in req.market.yes_book_asks],'no_ask_levels':[level.model_dump() for level in req.market.no_book_asks]})
 effective_exposure=d.size*d.paper_fill_fraction
 if effective_exposure>0:
  h.portfolio_heat+=effective_exposure;h.open_risk+=effective_exposure
 telemetry.inc('vesper_decisions_total',labels={'mode':h.mode.value,'action':action,'strategy':req.strategy_id});telemetry.set('vesper_portfolio_heat',h.portfolio_heat)
 memory.save_decision(d,h if effective_exposure>0 else None);memory.event('decision',d.model_dump())
 if req.execute and d.size>0:
  try:
   result=adapter_for(h.mode).execute(d);order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id=result['client_order_id'],decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price,status=OrderStatus(result['status']),filled_size=result.get('filled_size',0),average_fill_price=result.get('average_fill_price'));d.order_id=order.id;memory.save_order(order,d);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});memory.event('execution',order.model_dump())
  except Exception as exc:
   order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id='failed_'+os.urandom(6).hex(),decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.paper_execution_price if d.paper_execution_price is not None else d.executable_price if d.executable_price is not None else d.price,status=OrderStatus.FAILED,error=str(exc));d.order_id=order.id;memory.save_order(order,d);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});telemetry.error('order_execution');memory.event('execution_blocked',order.model_dump())
 return d
@app.post('/decide',response_model=DecisionRecord)
def decide(req:DecisionRequest,_=Depends(require_trade)):
 with memory.portfolio_lock():
  return _decide_impl(req)

@app.post('/outcomes',response_model=DecisionRecord)
def outcome(req:OutcomeRequest,_=Depends(require_trade)):
 with memory.decision_lock(req.decision_id):
  d=next((x for x in memory.decisions() if x.id==req.decision_id),None)
  if not d:raise HTTPException(404,'Decision not found')
  if d.outcome!='pending':raise HTTPException(409,'Decision already has a terminal outcome')
  if d.size<=0 or d.paper_fill_fraction<=0:raise HTTPException(409,'Cannot settle a decision with no filled exposure')
  settled_pnl=req.pnl
  if req.resolved_yes is not None and d.side in ('YES','NO'):
   won=req.resolved_yes==(d.side=='YES')
   if req.outcome in ('win','loss') and ((req.outcome=='win')!=won):raise HTTPException(422,'Outcome conflicts with resolved market result')
   if req.outcome in ('win','loss') and (d.paper_execution_price is not None or d.executable_price is not None):
    effective_size=d.size*d.paper_fill_fraction
    settlement_price=d.paper_execution_price if d.paper_execution_price is not None else d.executable_price
    settled_pnl=effective_size*(1-settlement_price) if won else -effective_size*settlement_price
  return settle_decision(memory,metrics_engine,scars,d,req.outcome,settled_pnl,req.clv,req.resolved_yes,req.evidence_complete,'operator',process_score=req.process_score)
@app.post('/demo/clear-learning')
def clear_learning(_=Depends(require_admin)):memory.delete_learning_memory();return {'message':'Learning memory removed; the agent returns to naive behavior.'}
@app.post('/demo/seed-market')
def seed_market(_=Depends(require_admin)):return {'market_id':'demo-market','message':'Use price 0.45, liquidity 25000, volume 100000, reference_rate 0.60.'}
