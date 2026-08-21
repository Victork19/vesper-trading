import os
from fastapi import FastAPI, HTTPException, Depends, Header, Request, Cookie
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from .models import *
from .config import settings
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
from .adapters import adapter_for
from .ingestion import IngestionStore
from .autonomy import AutonomyGate
from .observability import telemetry
from .security import SecurityManager
from .settlement import settle_decision
from datetime import datetime,timezone
import time,uuid,json,logging
app=FastAPI(title='Vesper Trading',version='6.0.0')
security=SecurityManager(settings)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173').split(','),allow_methods=['*'],allow_headers=['*'],allow_credentials=True)
log=logging.getLogger('vesper.api')

@app.middleware('http')
async def request_telemetry(request:Request,call_next):
 rid=request.headers.get('X-Request-ID') or 'req_'+uuid.uuid4().hex[:12];start=time.perf_counter();status=500
 if request.url.path not in ('/health','/ready') and request.headers.get('X-Vesper-Key'):
  try:security.check_rate(security.authenticate(request.headers.get('X-Vesper-Key'),'read').key_id,request.url.path)
  except HTTPException as exc:
   if exc.status_code==429:raise
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
memory=TradingMemory();edge=EdgeEngine();risk=RiskEngine(memory);portfolio=PortfolioRisk(memory);toxic=ToxicFlowDetector();bucket_killer=BucketKiller(memory);scars=ScarEngine(memory);metrics_engine=MetricsEngine(memory);markets=PolymarketData();graph=ExperienceGraph(memory);reference=ReferenceClassEngine();strategies=StrategyRegistry();secondary=SecondarySignals();ingestion_store=IngestionStore();autonomy=AutonomyGate(memory,ingestion_store)
@app.get('/health')
def health():telemetry.set('vesper_mode',{'paper':0,'shadow':1,'live':2}.get(memory.hot().mode.value,0));return {'status':'ok','service':'vesper-trading','mode':memory.hot().mode,'memory_load_bearing':True,'database':'postgresql','live_enabled':settings.live_enabled}
@app.get('/ready')
def ready():
 q=ingestion_store.quality();h=memory.hot();approval=memory.get('HOT','live_approval') or {}
 live_checks={'auth_configured':bool(settings.api_key and settings.admin_key),'limits_configured':settings.max_capital>0 and settings.max_order_size>0,'sample_gate':len(memory.decisions())>=settings.min_sample,'data_quality':q['score']>=settings.min_data_quality and not q['stale'],'operator_approved':bool(approval.get('active')),'execution_reconciled':False}
 checks={'api':True,'memory':memory.db.ping(),'data_quality':q['score']>=settings.min_data_quality or h.mode==Mode.PAPER,'data_fresh':not q['stale'] or h.mode==Mode.PAPER,'live_safe':all(live_checks) if h.mode==Mode.LIVE else True};return {'ready':all(checks.values()),'checks':checks,'live_checks':live_checks,'quality':q}
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
@app.get('/decisions',response_model=list[DecisionRecord])
def list_decisions(_=Depends(require_api_key)):return memory.decisions()
@app.get('/metrics',response_model=list[ProcessSnapshot])
def metrics(_=Depends(require_api_key)):return memory.snapshots()
@app.get('/replay/{decision_id}')
def replay(decision_id,_=Depends(require_api_key)):
 x=memory.replay(decision_id)
 if not x:raise HTTPException(404,'Decision not found')
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
  market=markets.market(market_id);tokens=market.get('clobTokenIds') or market.get('clobTokenIDs') or []
  if isinstance(tokens,str):
   try:tokens=json.loads(tokens)
   except json.JSONDecodeError:tokens=[]
  book=markets.book(str(tokens[0])) if isinstance(tokens,list) and tokens else None
  return markets.to_input(market,book)
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
def readiness(_=Depends(require_api_key)):return ready()
@app.get('/readiness/summary')
def readiness_summary(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();decisions=memory.decisions();resolved=[d for d in decisions if d.outcome!='pending'];pending=[d for d in decisions if d.outcome=='pending'];snapshots=memory.snapshots();resolved_count=len(resolved);wins=sum(1 for d in resolved if d.outcome=='win');pnl=sum(float(d.pnl) for d in resolved);minimum=settings.min_sample;blockers=[]
 if resolved_count<minimum:blockers.append(f'Need {minimum-resolved_count} more resolved paper outcomes before the live sample gate can pass.')
 if q['score']<settings.min_data_quality or q['stale']:blockers.append('Market data must remain fresh and above the configured quality threshold.')
 if not settings.live_enabled:blockers.append('LIVE_TRADING_ENABLED is false.')
 if settings.max_capital<=0 or settings.max_order_size<=0:blockers.append('Live capital and order limits are not configured.')
 if not (memory.get('HOT','live_approval') or {}).get('active'):blockers.append('Explicit operator live approval has not been granted.')
 blockers.append('Authenticated Polymarket CLOB execution and order reconciliation are not production-enabled.')
 learning='collecting' if not decisions else 'learning' if resolved_count<minimum else 'evidence_ready'
 return {'status':'paper_learning','learning_status':learning,'summary':f'{len(decisions)} paper decisions recorded; {resolved_count} resolved; {len(pending)} awaiting settlement.','automation':{'enabled':os.getenv('AUTO_PAPER_ENABLED','true').lower()=='true','decisions_per_tick':max(1,int(os.getenv('AUTO_PAPER_DECISIONS_PER_TICK','3'))),'cooldown_seconds':max(60,int(os.getenv('AUTO_PAPER_MARKET_COOLDOWN_SECONDS','21600')))},'paper':{'decisions':len(decisions),'resolved':resolved_count,'pending':len(pending),'wins':wins,'win_rate':wins/resolved_count if resolved_count else None,'pnl':pnl,'metrics_buckets':len(snapshots),'minimum_sample':minimum},'data':{'snapshots':q['snapshots'],'minimum_snapshots':int(os.getenv('MIN_MARKET_SNAPSHOTS','1000')),'quality':q['score'],'book_coverage':q.get('book_coverage',0),'stale':q['stale']},'worker':{'status':worker.get('status'),'last_resolved':worker.get('last_resolved',0),'last_pending':worker.get('last_pending',0)},'live':{'eligible':False,'blockers':blockers}}
@app.get('/metrics/prometheus',response_class=PlainTextResponse)
def prometheus_metrics(_=Depends(require_api_key)):return telemetry.prometheus()
@app.get('/observability')
def observability(_=Depends(require_api_key)):
 q=ingestion_store.quality();worker=ingestion_store.worker_health();telemetry.set('vesper_ingestion_quality_score',q['score']);telemetry.set('vesper_ingestion_stale',int(q['stale']));telemetry.set('vesper_worker_stale',int(worker.get('stale',True)));return {'telemetry':telemetry.snapshot(),'ingestion':q,'worker':worker,'ready':ready()}
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
@app.post('/decide',response_model=DecisionRecord)
def decide(req:DecisionRequest,_=Depends(require_trade)):
 h=memory.hot();strategy=strategies.get(req.strategy_id)
 if not strategy:raise HTTPException(422,f'Unknown strategy: {req.strategy_id}')
 history=[1 if d.resolved_yes else 0 for d in memory.decisions() if d.market_type==req.market.market_type and d.resolved_yes is not None]
 calibrated=reference.calibrated_prior(req.market.market_type,req.market.reference_rate if req.market.reference_rate is not None else .5,history)
 e=edge.estimate(req.market,calibrated);trust=memory.effective_trust(req.strategy_id,req.market.market_type,req.market.market_id)
 size,gates=risk.size(e,req.market,trust,strategy,settings.max_portfolio_heat)
 quality_gates=[]
 quote_time=req.market.quote_observed_at or req.market.observed_at
 if quote_time is not None and (datetime.now(timezone.utc)-quote_time).total_seconds()>120:quality_gates.append('stale_market_input')
 if req.market.quality_score<.95:quality_gates.append('market_quality_below_threshold')
 if req.market.market_status!='active':quality_gates.append('market_not_active')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and req.market.source=='manual':quality_gates.append('untrusted_market_source')
 if h.mode in (Mode.SHADOW,Mode.LIVE) and (req.market.yes_ask is None or req.market.no_ask is None):quality_gates.append('executable_quote_required')
 if quality_gates:size=0;gates+=quality_gates
 flow=toxic.inspect(req.market,req.flow_imbalance,req.large_wallet_signal);size,risk_reasons=portfolio.gate(req.market,size,req.flow_imbalance,req.large_wallet_signal);gates+=risk_reasons+flow['flags'];relevant=memory.active_scars(req.strategy_id,req.market.market_type,req.market.market_id);principles=[p for p in memory.principles() if p.status=='active' and p.strategy_id in (req.strategy_id,'global')];cited=[s.id for s in relevant];cp=[p.id for p in principles]
 if bucket_killer.suspended(req.strategy_id,req.market.market_type,req.market.regime):size=0;gates+=['bucket_suspended_negative_expectancy']
 if any(s.impact.constitutional and s.impact.max_size_multiplier<=0 for s in relevant):size=0;gates+=['scar_constitutional_stop']
 if not req.evidence_complete:size=0;gates+=['evidence_completeness_gate']
 if not flow['toxic'] and not any(x in gates for x in ['daily_kill_switch','weekly_kill_switch']):size*=flow['size_multiplier']
 if h.mode==Mode.LIVE:
  if os.getenv('LIVE_TRADING_ENABLED','false').lower()!='true':size=0;gates+=['live_operator_gate_disabled']
  if len(history)<settings.min_sample:size=0;gates+=['live_sample_gate']
  if settings.max_order_size<=0:size=0;gates+=['live_order_limit_gate']
  else:size=min(size,settings.max_order_size)
 action='DO NOTHING' if size<=0 else 'BUY';risk_score=min(10,max(1,int(e.raw_edge*100+(10 if relevant else 3))));rationale=('No trade: '+'; '.join(gates)) if size<=0 else 'Calibrated probability, executable side edge, liquidity, capacity, trust, scars, and portfolio gates passed.';status='paper' if h.mode==Mode.PAPER else 'shadow' if h.mode==Mode.SHADOW else 'live-gated'
 d=DecisionRecord(id='decision_'+os.urandom(5).hex(),mode=h.mode,market_id=req.market.market_id,strategy_id=req.strategy_id,market_type=req.market.market_type,regime=req.market.regime,action=action,side=e.recommended_side if size else None,size=size,price=req.market.price,fair_probability=e.fair_probability,confidence=e.confidence,risk_score=risk_score,edge=e.raw_edge,executable_price=e.executable_price,expected_value=e.raw_edge*size,rationale=rationale,cited_scars=cited,cited_principles=cp,gates=gates,status=status,source=req.market.source,quality_score=req.market.quality_score,snapshot_hash=req.market.snapshot_hash,observed_at=req.market.observed_at.isoformat() if req.market.observed_at else None,quote_observed_at=req.market.quote_observed_at.isoformat() if req.market.quote_observed_at else None,book_sequence=req.market.book_sequence)
 telemetry.inc('vesper_decisions_total',labels={'mode':h.mode.value,'action':action,'strategy':req.strategy_id});telemetry.set('vesper_portfolio_heat',h.portfolio_heat)
 memory.put('COLD',d.id,d.model_dump());memory.event('decision',d.model_dump())
 if req.execute and d.size>0:
  try:
   result=adapter_for(h.mode).execute(d);order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id=result['client_order_id'],decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.executable_price or d.price,status=OrderStatus(result['status']),filled_size=result.get('filled_size',0),average_fill_price=result.get('average_fill_price'));memory.save_order(order);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});d.order_id=order.id;memory.put('COLD',d.id,d.model_dump());memory.event('execution',order.model_dump())
  except Exception as exc:
   order=OrderRecord(id='order_'+os.urandom(6).hex(),client_order_id='failed_'+os.urandom(6).hex(),decision_id=d.id,mode=h.mode,market_id=d.market_id,side=d.side or 'UNKNOWN',requested_size=d.size,limit_price=d.executable_price or d.price,status=OrderStatus.FAILED,error=str(exc));memory.save_order(order);telemetry.inc('vesper_orders_total',labels={'mode':h.mode.value,'status':order.status.value});telemetry.error('order_execution');d.order_id=order.id;memory.put('COLD',d.id,d.model_dump());memory.event('execution_blocked',order.model_dump())
 return d
@app.post('/outcomes',response_model=DecisionRecord)
def outcome(req:OutcomeRequest,_=Depends(require_trade)):
 d=next((x for x in memory.decisions() if x.id==req.decision_id),None)
 if not d:raise HTTPException(404,'Decision not found')
 if d.outcome!='pending':raise HTTPException(409,'Decision already has a terminal outcome')
 if d.size<=0:raise HTTPException(409,'Cannot settle a decision with no exposure')
 settled_pnl=req.pnl
 if req.resolved_yes is not None and d.side in ('YES','NO'):
  won=req.resolved_yes==(d.side=='YES')
  if req.outcome in ('win','loss') and ((req.outcome=='win')!=won):raise HTTPException(422,'Outcome conflicts with resolved market result')
  if req.outcome in ('win','loss') and d.executable_price is not None:
   settled_pnl=d.size*(1-d.executable_price) if won else -d.size*d.executable_price
 return settle_decision(memory,metrics_engine,scars,d,req.outcome,settled_pnl,req.clv,req.resolved_yes,req.evidence_complete,'operator')
@app.post('/demo/clear-learning')
def clear_learning(_=Depends(require_admin)):memory.delete_learning_memory();return {'message':'Learning memory removed; the agent returns to naive behavior.'}
@app.post('/demo/seed-market')
def seed_market(_=Depends(require_admin)):return {'market_id':'demo-market','message':'Use price 0.45, liquidity 25000, volume 100000, reference_rate 0.60.'}
