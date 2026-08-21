import hashlib,json,os,threading
from contextlib import contextmanager
from datetime import datetime,timezone
from .db import PostgresDatabase
from .market_data import PolymarketData
from .observability import telemetry
from .resolver import OutcomeResolver

class IngestionStore:
 def __init__(self,database=None,path=None):self.db=database or PostgresDatabase();self.require_books=os.getenv('INGEST_REQUIRE_BOOKS','true').lower()=='true';self.require_both_books=os.getenv('INGEST_REQUIRE_BOTH_BOOKS','true').lower()=='true';self.lock=threading.RLock()
 def book_skew_seconds(self,market):
  books=[market.get('_vesper_book') or {},market.get('_vesper_no_book') or {}]
  stamps=[]
  for book in books:
   if book.get('observed_at'):
    try:stamps.append(datetime.fromisoformat(str(book['observed_at']).replace('Z','+00:00')))
    except (TypeError,ValueError):pass
  return (max(stamps)-min(stamps)).total_seconds() if len(stamps)>1 else 0.0
 @contextmanager
 def pipeline_lease(self):
  """Acquire a database-backed lease so only one worker runs a tick."""
  with self.db.connection() as c:
   acquired=c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('vesper:pipeline_tick',0)) AS acquired").fetchone()['acquired']
   yield bool(acquired)
 def save(self,market):
  with self.lock:
   market_id=str(market.get('id') or market.get('conditionId') or '')
   if not market_id:return False
   payload=json.dumps(market,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(payload.encode()).hexdigest();observed=datetime.now(timezone.utc).isoformat();reason=self.validation_reason(market);valid=reason is None;book=market.get('_vesper_book') or {};no_book=market.get('_vesper_no_book') or {};book_valid=bool(book.get('best_bid') is not None and book.get('best_ask') is not None and book.get('best_bid')<book.get('best_ask') and (not self.require_both_books or (no_book.get('best_bid') is not None and no_book.get('best_ask') is not None and no_book.get('best_bid')<no_book.get('best_ask'))) and self.book_skew_seconds(market)<=max(1,float(os.getenv('MAX_CONTRACT_QUOTE_SKEW_SECONDS','10'))));sequence=book.get('sequence')
   with self.db.connection() as c:
    inserted=c.execute('INSERT INTO market_snapshots(market_id,observed_at,payload,payload_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(payload_hash) DO NOTHING',(market_id,observed,self.db.json(market),digest)).rowcount==1;c.execute('INSERT INTO market_observations(market_id,observed_at,payload_hash,valid,validation_reason,book_valid,book_sequence) VALUES(%s,%s,%s,%s,%s,%s,%s)',(market_id,observed,digest,valid,reason,book_valid,sequence))
   telemetry.inc('vesper_market_observations_total',labels={'valid':str(int(valid)),'book_valid':str(int(book_valid))});return inserted
 def validate(self,market):
  return self.validation_reason(market) is None
 def validation_reason(self,market):
  try:
   if not str(market.get('question','')).strip():return 'missing_question'
   prices=market.get('outcomePrices',[.5]);prices=json.loads(prices) if isinstance(prices,str) else prices
   if not isinstance(prices,list) or not prices or any(float(x)<0 or float(x)>1 for x in prices):return 'invalid_outcome_prices'
   for key in ('liquidity','volume24h','volume24hr'):
    if key in market and market[key] not in (None,'') and float(market[key])<0:return 'negative_'+key
   if market.get('closed') and market.get('active'):return 'closed_and_active'
   book=market.get('_vesper_book') or {};no_book=market.get('_vesper_no_book') or {}
   if self.require_books and not book:return 'missing_order_book'
   if book and (book.get('best_bid') is None or book.get('best_ask') is None or book.get('best_bid')>=book.get('best_ask')):return 'invalid_order_book'
   if self.require_both_books and not no_book:return 'missing_no_order_book'
   if no_book and (no_book.get('best_bid') is None or no_book.get('best_ask') is None or no_book.get('best_bid')>=no_book.get('best_ask')):return 'invalid_no_order_book'
   if self.book_skew_seconds(market)>max(1,float(os.getenv('MAX_CONTRACT_QUOTE_SKEW_SECONDS','10'))):return 'incoherent_book_timestamps'
   return None
  except (TypeError,ValueError,json.JSONDecodeError):return 'malformed_market_payload'
 def save_verified_input(self,market_input):
  payload=self._provenance_payload(market_input);canonical=json.dumps(payload,sort_keys=True,separators=(',',':'));input_hash=hashlib.sha256(canonical.encode()).hexdigest();snapshot_hash=market_input.snapshot_hash or input_hash
  with self.db.connection() as c:
   existing=c.execute('SELECT market_id,input_hash FROM verified_market_inputs WHERE snapshot_hash=%s FOR UPDATE',(snapshot_hash,)).fetchone()
   if existing and (existing['market_id']!=market_input.market_id or existing['input_hash']!=input_hash):raise ValueError('verified snapshot hash is already bound to different market input')
   if not existing:c.execute('INSERT INTO verified_market_inputs(snapshot_hash,market_id,input_hash,payload,observed_at) VALUES(%s,%s,%s,%s,%s)',(snapshot_hash,market_input.market_id,input_hash,self.db.json(payload),market_input.observed_at or datetime.now(timezone.utc)))
  return snapshot_hash
 def verified_input_matches(self,market_input):
  if not market_input.snapshot_hash:return False
  payload=self._provenance_payload(market_input);canonical=json.dumps(payload,sort_keys=True,separators=(',',':'));input_hash=hashlib.sha256(canonical.encode()).hexdigest()
  with self.db.connection() as c:return c.execute('SELECT 1 FROM verified_market_inputs WHERE snapshot_hash=%s AND market_id=%s AND input_hash=%s',(market_input.snapshot_hash,market_input.market_id,input_hash)).fetchone() is not None
 def _provenance_payload(self,market_input):
  payload=market_input.model_dump(mode='json')
  for field in ('model_version','raw_model_probability','model_probability','model_lower_bound','model_upper_bound','model_uncertainty','model_calibration_samples','model_calibration_status'):
   payload.pop(field,None)
  if market_input.model_version:
   payload['reference_rate']=None
   payload['regime']='baseline'
   if payload.get('signals')=={'fast_model':market_input.model_probability}:payload['signals']={}
  return payload
 def verified_input(self,snapshot_hash):
  if not snapshot_hash:return None
  with self.db.connection() as c:
   row=c.execute('SELECT payload FROM verified_market_inputs WHERE snapshot_hash=%s',(snapshot_hash,)).fetchone()
   return row['payload'] if row else None
 def count(self):
  with self.db.connection() as c:return c.execute('SELECT COUNT(*) AS count FROM market_observations').fetchone()['count']
 def distinct_markets(self):
  with self.db.connection() as c:return c.execute('SELECT COUNT(DISTINCT market_id) AS count FROM market_observations').fetchone()['count']
 def quality(self):
  with self.db.connection() as c:row=c.execute("SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE NOT valid) AS invalid,COUNT(*) FILTER (WHERE NOT book_valid) AS invalid_books,MAX(observed_at) AS last FROM market_observations").fetchone();reason_rows=c.execute("SELECT COALESCE(validation_reason,'unknown') AS reason,COUNT(*) AS count FROM market_observations WHERE NOT valid GROUP BY validation_reason ORDER BY count DESC").fetchall()
  total=row['total'];invalid=row['invalid'];invalid_books=row['invalid_books'];last=row['last'];last_value=last.isoformat() if hasattr(last,'isoformat') else last;stale=not last or (datetime.now(timezone.utc)-last).total_seconds()>300
  return {'score':0 if not total else max(0,1-(invalid/total)),'snapshots':total,'missing_required_fields':invalid,'invalid_observations':invalid,'invalid_books':invalid_books,'invalid_reasons':{row['reason']:row['count'] for row in reason_rows},'book_coverage':0 if not total else max(0,1-(invalid_books/total)),'last_observed_at':last_value,'stale':stale}
 def status(self):
  with self.db.connection() as c:last=c.execute('SELECT MAX(observed_at) AS last FROM market_observations').fetchone()['last']
  return {'snapshots':self.count(),'distinct_markets':self.distinct_markets(),'last_observed_at':last.isoformat() if hasattr(last,'isoformat') else last,'quality':self.quality(),'worker':self.worker_health()}
 def record_heartbeat(self,markets=0,books=0,error=None):
  now=datetime.now(timezone.utc)
  with self.db.connection() as c:
   if error:c.execute('UPDATE pipeline_health SET last_tick_at=%s,last_error_at=%s,last_error=%s,error_count=error_count+1 WHERE id=1',(now,now,str(error)))
   else:c.execute('UPDATE pipeline_health SET last_tick_at=%s,last_success_at=%s,last_error=NULL,last_markets=%s,last_books=%s WHERE id=1',(now,now,markets,books))
 def record_resolution(self,result):
  with self.db.connection() as c:c.execute('UPDATE pipeline_health SET last_resolution_at=%s,last_resolved=%s,last_resolution_errors=%s,last_pending=%s WHERE id=1',(datetime.now(timezone.utc),result.get('settled',0),result.get('errors',0),result.get('pending',0)))
 def worker_health(self):
  with self.db.connection() as c:r=c.execute('SELECT last_tick_at,last_success_at,last_error_at,last_error,error_count,last_markets,last_books,last_resolution_at,last_resolved,last_resolution_errors,last_pending FROM pipeline_health WHERE id=1').fetchone()
  if not r:return {'status':'unknown'}
  data=dict(r);last=data.get('last_success_at') or data.get('last_tick_at');data['last_tick_at']=data['last_tick_at'].isoformat() if hasattr(data['last_tick_at'],'isoformat') else data['last_tick_at'];data['last_success_at']=data['last_success_at'].isoformat() if hasattr(data['last_success_at'],'isoformat') else data['last_success_at'];data['last_resolution_at']=data['last_resolution_at'].isoformat() if hasattr(data['last_resolution_at'],'isoformat') else data['last_resolution_at'];data['stale']=not last or (datetime.now(timezone.utc)-last).total_seconds()>180;data['status']='degraded' if data['stale'] or data.get('last_error') else 'healthy';return data

class IngestionRunner:
 def __init__(self):self.data=PolymarketData();self.store=IngestionStore();self.resolver=OutcomeResolver(data=self.data)
 def tick(self,limit=50):
  with self.store.pipeline_lease() as acquired:
   if not acquired:
    telemetry.inc('vesper_ingestion_ticks_skipped_total',labels={'reason':'pipeline_lease_busy'})
    return {'markets':0,'books':0,'new_snapshots':0,'observations':self.store.count(),'resolution':{'skipped':True,'reason':'pipeline_lease_busy'}}
   items=self.data.markets(limit);saved=0;books=0;telemetry.inc('vesper_ingestion_ticks_total')
   for item in items:
    enriched=dict(item);yes_token,no_token=self.data.token_pair(item)
    if yes_token:
     try:book=self.data.book(yes_token);enriched['_vesper_book']=book.model_dump();enriched['_vesper_yes_book']=book.model_dump();books+=1
     except Exception as exc:enriched['_vesper_book_error']=str(exc);telemetry.error('book_fetch')
    if no_token:
     try:no_book=self.data.book(no_token);enriched['_vesper_no_book']=no_book.model_dump();books+=1
     except Exception as exc:enriched['_vesper_no_book_error']=str(exc);telemetry.error('book_fetch')
    saved+=int(self.store.save(enriched))
   self.store.record_heartbeat(len(items),books)
  # Release the pipeline lease before resolution. Resolution takes a
  # decision lock and then a portfolio lock; keeping the lease connection
  # open here would exhaust a pool configured at the supported minimum size.
  resolution=self.resolver.tick();self.store.record_resolution(resolution);return {'markets':len(items),'books':books,'new_snapshots':saved,'observations':self.store.count(),'resolution':resolution}
