import hashlib,json,os,threading
from datetime import datetime,timezone
from .db import PostgresDatabase
from .market_data import PolymarketData
from .observability import telemetry
from .resolver import OutcomeResolver

class IngestionStore:
 def __init__(self,database=None,path=None):self.db=database or PostgresDatabase();self.require_books=os.getenv('INGEST_REQUIRE_BOOKS','true').lower()=='true';self.lock=threading.RLock()
 def save(self,market):
  with self.lock:
   market_id=str(market.get('id') or market.get('conditionId') or '')
   if not market_id:return False
   payload=json.dumps(market,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(payload.encode()).hexdigest();observed=datetime.now(timezone.utc).isoformat();valid=self.validate(market);book=market.get('_vesper_book') or {};book_valid=bool(book.get('best_bid') is not None and book.get('best_ask') is not None and book.get('best_bid')<book.get('best_ask'));sequence=book.get('sequence')
   with self.db.connection() as c:
    inserted=c.execute('INSERT INTO market_snapshots(market_id,observed_at,payload,payload_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(payload_hash) DO NOTHING',(market_id,observed,self.db.json(market),digest)).rowcount==1;c.execute('INSERT INTO market_observations(market_id,observed_at,payload_hash,valid,book_valid,book_sequence) VALUES(%s,%s,%s,%s,%s,%s)',(market_id,observed,digest,valid,book_valid,sequence))
   telemetry.inc('vesper_market_observations_total',labels={'valid':str(int(valid)),'book_valid':str(int(book_valid))});return inserted
 def validate(self,market):
  try:
   if not str(market.get('question','')).strip():return False
   prices=market.get('outcomePrices',[.5]);prices=json.loads(prices) if isinstance(prices,str) else prices
   if not isinstance(prices,list) or not prices or any(float(x)<0 or float(x)>1 for x in prices):return False
   for key in ('liquidity','volume24h','volume24hr'):
    if key in market and market[key] not in (None,'') and float(market[key])<0:return False
   if market.get('closed') and market.get('active'):return False
   book=market.get('_vesper_book') or {}
   if self.require_books and not book:return False
   if book and (book.get('best_bid') is None or book.get('best_ask') is None or book.get('best_bid')>=book.get('best_ask')):return False
   return True
  except (TypeError,ValueError,json.JSONDecodeError):return False
 def count(self):
  with self.db.connection() as c:return c.execute('SELECT COUNT(*) AS count FROM market_observations').fetchone()['count']
 def distinct_markets(self):
  with self.db.connection() as c:return c.execute('SELECT COUNT(DISTINCT market_id) AS count FROM market_observations').fetchone()['count']
 def quality(self):
  with self.db.connection() as c:row=c.execute("SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE NOT valid) AS invalid,MAX(observed_at) AS last FROM market_observations").fetchone()
  total=row['total'];invalid=row['invalid'];last=row['last'];last_value=last.isoformat() if hasattr(last,'isoformat') else last;stale=not last or (datetime.now(timezone.utc)-last).total_seconds()>300
  return {'score':0 if not total else max(0,1-(invalid/total)),'snapshots':total,'missing_required_fields':invalid,'last_observed_at':last_value,'stale':stale}
 def status(self):
  with self.db.connection() as c:last=c.execute('SELECT MAX(observed_at) AS last FROM market_observations').fetchone()['last']
  return {'snapshots':self.count(),'distinct_markets':self.distinct_markets(),'last_observed_at':last.isoformat() if hasattr(last,'isoformat') else last,'quality':self.quality(),'worker':self.worker_health()}
 def record_heartbeat(self,markets=0,books=0,error=None):
  now=datetime.now(timezone.utc)
  with self.db.connection() as c:
   if error:c.execute('UPDATE pipeline_health SET last_tick_at=%s,last_error_at=%s,last_error=%s,error_count=error_count+1 WHERE id=1',(now,now,str(error)))
   else:c.execute('UPDATE pipeline_health SET last_tick_at=%s,last_success_at=%s,last_error=NULL,last_markets=%s,last_books=%s WHERE id=1',(now,now,markets,books))
 def worker_health(self):
  with self.db.connection() as c:r=c.execute('SELECT last_tick_at,last_success_at,last_error_at,last_error,error_count,last_markets,last_books FROM pipeline_health WHERE id=1').fetchone()
  if not r:return {'status':'unknown'}
  data=dict(r);last=data.get('last_success_at') or data.get('last_tick_at');data['last_tick_at']=data['last_tick_at'].isoformat() if hasattr(data['last_tick_at'],'isoformat') else data['last_tick_at'];data['last_success_at']=data['last_success_at'].isoformat() if hasattr(data['last_success_at'],'isoformat') else data['last_success_at'];data['stale']=not last or (datetime.now(timezone.utc)-last).total_seconds()>180;data['status']='degraded' if data['stale'] or data.get('last_error') else 'healthy';return data

class IngestionRunner:
 def __init__(self):self.data=PolymarketData();self.store=IngestionStore();self.resolver=OutcomeResolver(data=self.data)
 def tick(self,limit=50):
  items=self.data.markets(limit);saved=0;books=0;telemetry.inc('vesper_ingestion_ticks_total')
  for item in items:
   enriched=dict(item);tokens=item.get('clobTokenIds') or item.get('clobTokenIDs') or []
   if isinstance(tokens,str):
    try:tokens=json.loads(tokens)
    except json.JSONDecodeError:tokens=[]
   if isinstance(tokens,list) and tokens:
    try:book=self.data.book(str(tokens[0]));enriched['_vesper_book']=book.model_dump();books+=1
    except Exception as exc:enriched['_vesper_book_error']=str(exc);telemetry.error('book_fetch')
   saved+=int(self.store.save(enriched))
  self.store.record_heartbeat(len(items),books);resolution=self.resolver.tick();return {'markets':len(items),'books':books,'new_snapshots':saved,'observations':self.store.count(),'resolution':resolution}
