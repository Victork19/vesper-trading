import hashlib,json,sqlite3,time,os,threading
from datetime import datetime,timezone
from .market_data import PolymarketData
from .observability import telemetry
from .resolver import OutcomeResolver
class IngestionStore:
 def __init__(self,path=None):
  self.path=path or os.getenv('SIBYL_DB_PATH','./data/trading.db');self.require_books=os.getenv('INGEST_REQUIRE_BOOKS','true').lower()=='true';self.lock=threading.RLock();self.db=sqlite3.connect(self.path,timeout=30,check_same_thread=False);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA busy_timeout=30000');self.db.execute('CREATE TABLE IF NOT EXISTS market_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,market_id TEXT,observed_at TEXT,payload TEXT,payload_hash TEXT UNIQUE)');self.db.execute('CREATE TABLE IF NOT EXISTS market_observations(id INTEGER PRIMARY KEY AUTOINCREMENT,market_id TEXT,observed_at TEXT,payload_hash TEXT,valid INTEGER NOT NULL,book_valid INTEGER NOT NULL DEFAULT 0,book_sequence INTEGER)');self.db.execute('CREATE INDEX IF NOT EXISTS idx_market_observations_observed ON market_observations(observed_at)')
  try:self.db.execute('ALTER TABLE market_observations ADD COLUMN book_valid INTEGER NOT NULL DEFAULT 0')
  except sqlite3.OperationalError:pass
  try:self.db.execute('ALTER TABLE market_observations ADD COLUMN book_sequence INTEGER')
  except sqlite3.OperationalError:pass
  self.db.execute('CREATE TABLE IF NOT EXISTS pipeline_health(id INTEGER PRIMARY KEY CHECK(id=1),last_tick_at TEXT,last_success_at TEXT,last_error_at TEXT,last_error TEXT,error_count INTEGER NOT NULL DEFAULT 0,last_markets INTEGER NOT NULL DEFAULT 0,last_books INTEGER NOT NULL DEFAULT 0)');self.db.execute('INSERT OR IGNORE INTO pipeline_health(id) VALUES(1)');self.db.commit()
  try:self.db.execute('ALTER TABLE market_snapshots ADD COLUMN payload_hash TEXT')
  except sqlite3.OperationalError:pass
  self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_market_snapshots_hash ON market_snapshots(payload_hash)');self.db.execute('CREATE INDEX IF NOT EXISTS idx_market_snapshots_observed ON market_snapshots(observed_at)');self.db.commit()
 def save(self,market):
  with self.lock:
   market_id=str(market.get('id') or market.get('conditionId') or '')
   if not market_id:return False
   payload=json.dumps(market,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(payload.encode()).hexdigest()
   observed=datetime.now(timezone.utc).isoformat();valid=self.validate(market)
   book=market.get('_vesper_book') or {};book_valid=bool(book.get('best_bid') is not None and book.get('best_ask') is not None and book.get('best_bid')<book.get('best_ask'));sequence=book.get('sequence');before=self.db.total_changes;self.db.execute('INSERT OR IGNORE INTO market_snapshots(market_id,observed_at,payload,payload_hash) VALUES(?,?,?,?)',(market_id,observed,payload,digest));self.db.execute('INSERT INTO market_observations(market_id,observed_at,payload_hash,valid,book_valid,book_sequence) VALUES(?,?,?,?,?,?)',(market_id,observed,digest,int(valid),int(book_valid),sequence));self.db.commit();telemetry.inc('vesper_market_observations_total',labels={'valid':str(int(valid)),'book_valid':str(int(book_valid))});return self.db.total_changes>before
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
 def count(self):return self.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0]
 def distinct_markets(self):return self.db.execute('SELECT COUNT(DISTINCT market_id) FROM market_observations').fetchone()[0]
 def quality(self):
  total=self.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0];invalid=self.db.execute('SELECT COUNT(*) FROM market_observations WHERE valid=0').fetchone()[0];last=self.db.execute('SELECT MAX(observed_at) FROM market_observations').fetchone()[0]
  return {'score':0 if not total else max(0,1-(invalid/total)),'snapshots':total,'missing_required_fields':invalid,'last_observed_at':last,'stale':not last or (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>300}
 def status(self):return {'snapshots':self.count(),'distinct_markets':self.distinct_markets(),'last_observed_at':self.db.execute('SELECT MAX(observed_at) FROM market_observations').fetchone()[0],'quality':self.quality(),'worker':self.worker_health()}
 def record_heartbeat(self,markets=0,books=0,error=None):
  now=datetime.now(timezone.utc).isoformat()
  if error:self.db.execute('UPDATE pipeline_health SET last_tick_at=?,last_error_at=?,last_error=?,error_count=error_count+1 WHERE id=1',(now,now,str(error)))
  else:self.db.execute('UPDATE pipeline_health SET last_tick_at=?,last_success_at=?,last_error=NULL,last_markets=?,last_books=? WHERE id=1',(now,now,markets,books))
  self.db.commit()
 def worker_health(self):
  row=self.db.execute('SELECT last_tick_at,last_success_at,last_error_at,last_error,error_count,last_markets,last_books FROM pipeline_health WHERE id=1').fetchone()
  if not row:return {'status':'unknown'}
  data=dict(zip(('last_tick_at','last_success_at','last_error_at','last_error','error_count','last_markets','last_books'),row));last=data.get('last_success_at') or data.get('last_tick_at');data['stale']=not last or (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>180;data['status']='degraded' if data['stale'] or data.get('last_error') else 'healthy';return data
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
    try:
     book=self.data.book(str(tokens[0]));enriched['_vesper_book']=book.model_dump();books+=1
    except Exception as exc:enriched['_vesper_book_error']=str(exc);telemetry.error('book_fetch')
   saved+=int(self.store.save(enriched))
  self.store.record_heartbeat(len(items),books);resolution=self.resolver.tick();return {'markets':len(items),'books':books,'new_snapshots':saved,'observations':self.store.count(),'resolution':resolution}
