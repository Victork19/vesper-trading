import threading,uuid
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from .db import PostgresDatabase
from .models import *

class TradingMemory:
 def __init__(self,database=None,path=None):
  self.db=database or PostgresDatabase();self.lock=threading.RLock()
  if not self.get('HOT','state'): self.put('HOT','state',HotState().model_dump())
  if not self.get('REFERENCE','constitution'): self.put('REFERENCE','constitution',{'rules':['No live trading by default.','No trade without sufficient liquidity and evidence.','Do nothing is always allowed.','Scars can only tighten constraints.','Never bypass a kill switch.']})
 def put(self,tier,key,value):
  with self.lock:
   with self.db.connection() as c:c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',(tier,key,self.db.json(value),now_iso()))
 def get(self,tier,key):
  with self.db.connection() as c:
   r=c.execute('SELECT value FROM memory WHERE tier=%s AND key=%s',(tier,key)).fetchone();return r['value'] if r else None
 def all(self,tier):
  with self.db.connection() as c:return [r['value'] for r in c.execute('SELECT value FROM memory WHERE tier=%s ORDER BY updated_at DESC',(tier,)).fetchall()]
 def hot(self):return HotState.model_validate(self.get('HOT','state') or {})
 def save_hot(self,x):self.put('HOT','state',x.model_dump())
 def save_decision(self,decision,hot=None):
  """Persist a decision and optional heat reservation in one transaction."""
  with self.lock:
   with self.db.connection() as c:
    if hot is not None:
     c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('HOT','state',self.db.json(hot.model_dump()),now_iso()))
    c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',decision.id,self.db.json(decision.model_dump()),now_iso()))
 def save_settlement_state(self,decision,hot,connection=None):
  """Persist terminal decision state and released heat atomically."""
  def write(c):
   timestamp=now_iso()
   c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('HOT','state',self.db.json(hot.model_dump()),timestamp))
   c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',decision.id,self.db.json(decision.model_dump()),timestamp))
  with self.lock:
   if connection is not None:write(connection)
   else:
    with self.db.connection() as c:write(c)
 def scars(self):
  with self.db.connection() as c:return [Scar.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='WARM' AND value ? 'lesson' ORDER BY updated_at DESC").fetchall()]
 def principles(self):
  with self.db.connection() as c:return [Principle.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='WARM' AND value ? 'statement' ORDER BY updated_at DESC").fetchall()]
 def decisions(self):
  with self.db.connection() as c:return [DecisionRecord.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='COLD' AND value ? 'action' ORDER BY updated_at DESC").fetchall()]
 def save_order(self,order,decision=None):
  allowed={
   'new':{'new','accepted','rejected','failed'},
   'accepted':{'accepted','partially_filled','filled','canceled','rejected','failed'},
   'partially_filled':{'partially_filled','filled','canceled','failed'},
   'filled':{'filled'},'canceled':{'canceled'},'rejected':{'rejected'},'failed':{'failed'}
  }
  with self.lock:
   with self.db.connection() as c:
    existing=c.execute('SELECT status FROM orders WHERE id=%s FOR UPDATE',(order.id,)).fetchone();new_status=order.status.value
    if existing and new_status not in allowed.get(existing['status'],set()):raise ValueError(f'illegal order transition: {existing["status"]} -> {new_status}')
    if order.filled_size<0 or order.filled_size>order.requested_size:raise ValueError('filled size must be within requested size')
    c.execute('''INSERT INTO orders(id,client_order_id,decision_id,mode,market_id,side,requested_size,limit_price,status,filled_size,average_fill_price,venue_order_id,error,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,filled_size=EXCLUDED.filled_size,average_fill_price=EXCLUDED.average_fill_price,venue_order_id=EXCLUDED.venue_order_id,error=EXCLUDED.error,updated_at=EXCLUDED.updated_at''',(order.id,order.client_order_id,order.decision_id,order.mode.value,order.market_id,order.side,order.requested_size,order.limit_price,order.status.value,order.filled_size,order.average_fill_price,order.venue_order_id,order.error,order.created_at,order.updated_at))
    if decision is not None:
     c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',decision.id,self.db.json(decision.model_dump()),now_iso()))
 def _order(self,row):
  if not row:return None
  data=dict(row)
  for key in ('created_at','updated_at'):
   if hasattr(data.get(key),'isoformat'):data[key]=data[key].isoformat().replace('+00:00','Z')
  return OrderRecord.model_validate(data)
 def order(self,order_id):
  with self.db.connection() as c:
   return self._order(c.execute('SELECT * FROM orders WHERE id=%s',(order_id,)).fetchone())
 def orders(self):
  with self.db.connection() as c:return [self._order(r) for r in c.execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()]
 def snapshots(self):
  with self.db.connection() as c:return [ProcessSnapshot.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='WARM' AND value ? 'expectancy' ORDER BY updated_at DESC").fetchall()]
 def active_scars(self,strategy_id='unknown',market_type='unknown',market_id='unknown',regime='unknown'):
  now=datetime.now(timezone.utc);result=[]
  for scar in self.scars():
   bucket_match=scar.market_id in (market_id,'unknown','global') or (scar.market_type in (market_type,'unknown','global') and scar.regime in (regime,'unknown','global'))
   if scar.status not in ('active','rehabilitating') or scar.strategy_id not in (strategy_id,'unknown','global') or not bucket_match:continue
   try:
    created=datetime.fromisoformat(scar.created_at.replace('Z','+00:00'))
    if created+timedelta(hours=scar.impact.cooldown_hours)>now:result.append(scar);continue
   except ValueError:pass
   result.append(scar)
  # Exact market scars dominate broad regime scars. The ordering is part of
  # the memory contract: callers can cite the highest-impact lessons first.
  return sorted(result,key=lambda scar:(
   0 if scar.market_id==market_id else 1 if scar.market_id not in ('unknown','global') else 2,
   0 if scar.market_type==market_type else 1,
   -scar.severity,-scar.evidence_count
  ))
 def memory_digest(self,strategy_id='unknown',market_type='unknown',market_id='unknown',regime='unknown',limit=8):
  scars=self.active_scars(strategy_id,market_type,market_id,regime)[:limit]
  principles=[p for p in self.principles() if p.status=='active' and p.strategy_id in (strategy_id,'global') and p.regime in (regime,'global')]
  return {'scars':[{'id':s.id,'severity':s.severity,'failure_type':s.failure_type,'lesson':s.lesson,'size_multiplier':s.impact.max_size_multiplier,'evidence_count':s.evidence_count,'recovery_score':s.recovery_score} for s in scars],'principles':[{'id':p.id,'statement':p.statement,'strength':p.strength} for p in principles[:limit]]}
 def effective_trust(self,strategy_id,market_type='unknown',market_id='unknown',regime='unknown'):
  value=self.hot().trust.get(strategy_id,.5)
  scars=self.active_scars(strategy_id,market_type,market_id,regime)
  for scar in scars:value+=scar.impact.trust_delta
  return max(.25 if scars else 0,min(1,value))
 def scar_size_multiplier(self,strategy_id,market_type='unknown',market_id='unknown',regime='unknown'):
  value=1.0
  for scar in self.active_scars(strategy_id,market_type,market_id,regime):value*=max(0,min(1,scar.impact.max_size_multiplier))
  return max(0.05,min(1,value))
 def event(self,name,payload):
  with self.lock:
   event_id='event_'+uuid.uuid4().hex;created=now_iso();body={'event':name,'payload':payload,'created_at':created,'event_id':event_id}
   with self.db.connection() as c:
    c.execute('INSERT INTO journal(event_id,created_at,event,payload) VALUES(%s,%s,%s,%s)',(event_id,created,name,self.db.json(payload)))
    c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',event_id,self.db.json(body),created))
   return body
 @contextmanager
 def decision_lock(self,decision_id):
  """Serialize settlement attempts for one decision across API workers."""
  with self.db.connection() as c:
   c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(decision_id),))
   yield
 @contextmanager
 def portfolio_lock(self):
  """Serialize sizing/reservation and heat release across workers."""
  with self.db.connection() as c:
   c.execute("SELECT pg_advisory_xact_lock(hashtextextended('vesper:portfolio',0))")
   yield c
 def events(self,limit=200):
  with self.db.connection() as c:return [dict(r) for r in c.execute('SELECT seq,event_id,created_at,event,payload FROM journal ORDER BY seq DESC LIMIT %s',(limit,)).fetchall()]
 def audit(self,limit=200):return [x for x in self.events(limit) if x['event'] in ('decision','outcome_recorded','scar_created','mode_changed','operator_approval','execution','execution_blocked','kill_switch','data_quality','learning_memory_deleted')]
 def delete_learning_memory(self):
  with self.lock:
   with self.db.connection() as c:c.execute("DELETE FROM memory WHERE tier IN ('HOT','WARM','ARCHIVE')")
   self.put('HOT','state',HotState().model_dump())
   self.event('learning_memory_deleted',{'reason':'operator_request'})
 def replay(self,decision_id):
  with self.db.connection() as c:
   r=c.execute("SELECT payload FROM journal WHERE event='decision' AND payload->>'id'=%s ORDER BY seq ASC LIMIT 1",(decision_id,)).fetchone();return r['payload'] if r else next((x for x in self.all('COLD') if x.get('id')==decision_id),None)
