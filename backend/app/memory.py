import json,os,sqlite3,uuid,threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .models import *
class TradingMemory:
 def __init__(self,path=None):
  self.path=Path(path or os.getenv('SIBYL_DB_PATH','./data/trading.db'));self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.db=sqlite3.connect(self.path,timeout=30,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA busy_timeout=30000');self.db.execute('PRAGMA foreign_keys=ON');self.db.execute('CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL)');self.db.execute('CREATE TABLE IF NOT EXISTS memory(tier TEXT,key TEXT,value TEXT,updated_at TEXT,PRIMARY KEY(tier,key))');self.db.execute('CREATE TABLE IF NOT EXISTS journal(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,created_at TEXT,event TEXT,payload TEXT)');self.db.execute('CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,client_order_id TEXT UNIQUE,decision_id TEXT,mode TEXT,market_id TEXT,side TEXT,requested_size REAL,limit_price REAL,status TEXT,filled_size REAL,average_fill_price REAL,venue_order_id TEXT,error TEXT,created_at TEXT,updated_at TEXT)');self.db.execute('CREATE INDEX IF NOT EXISTS idx_orders_decision ON orders(decision_id)');self.db.execute('CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)');self.db.commit();self.official=None
  if os.getenv('SIBYL_OFFICIAL','1')!='0':
   try:
    from sibyl_memory_client import MemoryClient;self.official=MemoryClient.local(str(self.path))
   except Exception: pass
  if not self.get('HOT','state'): self.put('HOT','state',HotState().model_dump())
  if not self.get('REFERENCE','constitution'): self.put('REFERENCE','constitution',{'rules':['No live trading by default.','No trade without sufficient liquidity and evidence.','Do nothing is always allowed.','Scars can only tighten constraints.','Never bypass a kill switch.']})
 def put(self,tier,key,value):
  with self.lock:self.db.execute('INSERT OR REPLACE INTO memory VALUES(?,?,?,?)',(tier,key,json.dumps(value),now_iso()));self.db.commit();self._official(tier,key,value)
 def _official(self,tier,key,value):
  if not self.official:return
  try:
   fn={'HOT':'set_state','WARM':'set_entity','REFERENCE':'set_reference','COLD':'write_event'}.get(tier)
   if fn and hasattr(self.official,fn): getattr(self.official,fn)('vesper-trading',key,value)
  except Exception: pass
 def get(self,tier,key):
  r=self.db.execute('SELECT value FROM memory WHERE tier=? AND key=?',(tier,key)).fetchone();return json.loads(r['value']) if r else None
 def all(self,tier): return [json.loads(r['value']) for r in self.db.execute('SELECT value FROM memory WHERE tier=? ORDER BY updated_at DESC',(tier,))]
 def hot(self): return HotState.model_validate(self.get('HOT','state') or {})
 def save_hot(self,x): self.put('HOT','state',x.model_dump())
 def scars(self): return [Scar.model_validate(x) for x in self.all('WARM') if 'lesson' in x]
 def principles(self): return [Principle.model_validate(x) for x in self.all('WARM') if 'statement' in x]
 def decisions(self): return [DecisionRecord.model_validate(x) for x in self.all('COLD') if 'action' in x]
 def save_order(self,order):
  with self.lock:
   self.db.execute('INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(order.id,order.client_order_id,order.decision_id,order.mode.value,order.market_id,order.side,order.requested_size,order.limit_price,order.status.value,order.filled_size,order.average_fill_price,order.venue_order_id,order.error,order.created_at,order.updated_at));self.db.commit()
 def order(self,order_id):
  row=self.db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone();return OrderRecord.model_validate(dict(row)) if row else None
 def orders(self):return [OrderRecord.model_validate(dict(x)) for x in self.db.execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()]
 def snapshots(self): return [ProcessSnapshot.model_validate(x) for x in self.all('WARM') if 'expectancy' in x]
 def active_scars(self, strategy_id='unknown', market_type='unknown', market_id='unknown'):
  now=datetime.now(timezone.utc);result=[]
  for scar in self.scars():
   if scar.status!='active' or scar.strategy_id not in (strategy_id,'unknown','global') or scar.market_type not in (market_type,'unknown','global') or scar.market_id not in (market_id,'unknown','global'): continue
   try:
    created=datetime.fromisoformat(scar.created_at.replace('Z','+00:00'))
    if created + timedelta(hours=scar.impact.cooldown_hours) > now: result.append(scar); continue
   except ValueError: pass
   result.append(scar)
  return result
 def effective_trust(self,strategy_id,market_type='unknown',market_id='unknown'):
  value=self.hot().trust.get(strategy_id,.5)
  for scar in self.active_scars(strategy_id,market_type,market_id): value += scar.impact.trust_delta
  return max(0,min(1,value))
 def event(self,name,payload):
  with self.lock:
   event_id='event_'+uuid.uuid4().hex;created=now_iso();body={'event':name,'payload':payload,'created_at':created,'event_id':event_id}
   self.db.execute('INSERT INTO journal(event_id,created_at,event,payload) VALUES(?,?,?,?)',(event_id,created,name,json.dumps(payload)));self.db.commit();self.put('COLD',event_id,body);return body
 def events(self,limit=200):
  return [dict(x) for x in self.db.execute('SELECT seq,event_id,created_at,event,payload FROM journal ORDER BY seq DESC LIMIT ?',(limit,)).fetchall()]
 def audit(self,limit=200):return [x for x in self.events(limit) if x['event'] in ('decision','outcome_recorded','scar_created','mode_changed','operator_approval','execution','execution_blocked','kill_switch','data_quality','learning_memory_deleted')]
 def delete_learning_memory(self):
  with self.lock:self.db.execute("DELETE FROM memory WHERE tier IN ('HOT','WARM','ARCHIVE')");self.db.commit();self.put('HOT','state',HotState().model_dump());self.event('learning_memory_deleted',{'reason':'operator_request'})
 def replay(self,decision_id):
  row=self.db.execute("SELECT payload FROM journal WHERE event='decision' AND json_extract(payload,'$.id')=? ORDER BY seq ASC LIMIT 1",(decision_id,)).fetchone()
  return json.loads(row['payload']) if row else next((x for x in self.all('COLD') if x.get('id')==decision_id),None)
