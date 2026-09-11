import threading,uuid
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from .db import PostgresDatabase
from .models import *
from .eda import default_objective_policy, episode_hash, insert_canonical_event_json, make_canonical_event, validate_episode_integrity

class TradingMemory:
 def __init__(self,database=None,path=None):
  self.db=database or PostgresDatabase();self.lock=threading.RLock()
  if not self.get('HOT','state'): self.put('HOT','state',HotState().model_dump())
  if not self.get('REFERENCE','constitution'): self.put('REFERENCE','constitution',{'rules':['No live trading by default.','No trade without sufficient liquidity and evidence.','Do nothing is always allowed.','Scars can only tighten constraints.','Never bypass a kill switch.']})
  if not self.get('REFERENCE','objective_policy'): self.put('REFERENCE','objective_policy',default_objective_policy().model_dump())
 def put(self,tier,key,value):
  with self.lock:
   with self.db.connection() as c:c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',(tier,key,self.db.json(value),now_iso()))
 def get(self,tier,key):
  with self.db.connection() as c:
   r=c.execute('SELECT value FROM memory WHERE tier=%s AND key=%s',(tier,key)).fetchone();return r['value'] if r else None
 def all(self,tier):
  with self.db.connection() as c:return [r['value'] for r in c.execute('SELECT value FROM memory WHERE tier=%s ORDER BY updated_at DESC',(tier,)).fetchall()]
 def hot(self):
  # Calendar rollover is a state transition, not a read-side convenience.
  # Lock the row so two API/worker processes cannot reset a newly accumulated
  # PnL/heat projection from stale snapshots.
  with self.db.connection() as c:
   row=c.execute("SELECT value FROM memory WHERE tier='HOT' AND key='state' FOR UPDATE").fetchone()
   state=HotState.model_validate(row['value'] if row else {})
   day,week=pnl_bucket_keys();changed=False
   if state.pnl_day!=day:state.daily_pnl=0;state.pnl_day=day;changed=True
   if state.pnl_week!=week:state.weekly_pnl=0;state.pnl_week=week;changed=True
   if changed:
    c.execute("INSERT INTO memory(tier,key,value,updated_at) VALUES('HOT','state',%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at",(self.db.json(state.model_dump()),now_iso()))
   return state
 def save_hot(self,x):self.put('HOT','state',x.model_dump())
 def hot_for_update(self,connection):
  """Load the hot state under the caller's transaction/row lock."""
  row=connection.execute("SELECT value FROM memory WHERE tier='HOT' AND key='state' FOR UPDATE").fetchone()
  state=HotState.model_validate(row['value'] if row else {})
  day,week=pnl_bucket_keys()
  if state.pnl_day!=day:
   state.daily_pnl=0;state.pnl_day=day
  if state.pnl_week!=week:
   state.weekly_pnl=0;state.pnl_week=week
  return state
 def save_decision(self,decision,hot=None,episode=None,eda_events=None):
  """Persist a decision and optional heat reservation in one transaction."""
  with self.lock:
   with self.db.connection() as c:
    if hot is not None:
     c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('HOT','state',self.db.json(hot.model_dump()),now_iso()))
    c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',decision.id,self.db.json(decision.model_dump()),now_iso()))
    if episode is not None:
     validate_episode_integrity(episode)
     payload=episode.model_dump(mode='json')
     c.execute('''INSERT INTO eda_episodes(episode_id,decision_id,created_at,timestamp_decision,episode,episode_hash,schema_version)
                  VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(episode_id) DO NOTHING''',(episode.episode_id,episode.decision_id,episode.timestamp_start,episode.timestamp_decision,self.db.json(payload),episode_hash(episode),episode.schema_version))
    for event in eda_events or []: insert_canonical_event_json(c,self.db,event)
 def save_settlement_state(self,decision,hot,connection=None,eda_events=None):
  """Persist terminal decision state and released heat atomically."""
  def write(c):
   timestamp=now_iso()
   c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('HOT','state',self.db.json(hot.model_dump()),timestamp))
   c.execute('INSERT INTO memory(tier,key,value,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT(tier,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at',('COLD',decision.id,self.db.json(decision.model_dump()),timestamp))
  for event in eda_events or []: insert_canonical_event_json(c,self.db,event)
  with self.lock:
   if connection is not None:write(connection)
   else:
    with self.db.connection() as c:write(c)
 def settle_reservations(self,decision_id,connection):
  """Atomically close reservations belonging to a settled decision."""
  rows=connection.execute("SELECT reservation_id,state FROM capital_reservations WHERE decision_id=%s FOR UPDATE",(decision_id,)).fetchall()
  for row in rows:
   if row['state'] in ('released','settled'): continue
   connection.execute("UPDATE capital_reservations SET state='settled',released_at=COALESCE(released_at,NOW()) WHERE reservation_id=%s",(row['reservation_id'],))
  return len(rows)
 def scars(self):
  with self.db.connection() as c:return [Scar.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='WARM' AND value ? 'lesson' ORDER BY updated_at DESC").fetchall()]
 def principles(self):
  with self.db.connection() as c:return [Principle.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='WARM' AND value ? 'statement' ORDER BY updated_at DESC").fetchall()]
 def decisions(self):
  with self.db.connection() as c:return [DecisionRecord.model_validate(r['value']) for r in c.execute("SELECT value FROM memory WHERE tier='COLD' AND value ? 'action' ORDER BY updated_at DESC").fetchall()]
 def save_order(self,order,decision=None):
  allowed={
   'new':{'new','accepted','rejected','failed','unknown','reconciliation_required'},
   'accepted':{'accepted','partially_filled','filled','canceled','cancel_requested','expired','rejected','failed','unknown','reconciliation_required'},
   'partially_filled':{'partially_filled','filled','canceled','cancel_requested','expired','failed','unknown','reconciliation_required'},
   'cancel_requested':{'cancel_requested','canceled','filled','partially_filled','expired','unknown','reconciliation_required'},
   'unknown':{'unknown','accepted','partially_filled','filled','canceled','cancel_requested','expired','rejected','failed','reconciliation_required'},
   'reconciliation_required':{'reconciliation_required','accepted','partially_filled','filled','canceled','cancel_requested','expired','rejected','failed','unknown'},
   'filled':{'filled'},'canceled':{'canceled'},'expired':{'expired'},'rejected':{'rejected'},'failed':{'failed'}
  }
  with self.lock:
   with self.db.connection() as c:
    existing=c.execute('SELECT status,filled_size,filled_notional,filled_fees,average_fill_price FROM orders WHERE id=%s FOR UPDATE',(order.id,)).fetchone();new_status=order.status.value
    if existing and new_status not in allowed.get(existing['status'],set()):raise ValueError(f'illegal order transition: {existing["status"]} -> {new_status}')
    if order.filled_size<0 or order.filled_size>order.requested_size:raise ValueError('filled size must be within requested size')
    if existing and order.filled_size+1e-12<float(existing['filled_size']):raise ValueError('filled size cannot decrease during reconciliation')
    if existing and order.filled_notional+1e-9<float(existing['filled_notional'] or 0):raise ValueError('filled notional cannot decrease during reconciliation')
    if existing and order.filled_fees+1e-9<float(existing['filled_fees'] or 0):raise ValueError('filled fees cannot decrease during reconciliation')
    if existing and existing['status'] in ('filled','canceled','expired','rejected','failed') and (abs(order.filled_size-float(existing['filled_size'] or 0))>1e-12 or abs(order.filled_notional-float(existing['filled_notional'] or 0))>1e-9 or abs(order.filled_fees-float(existing['filled_fees'] or 0))>1e-9):raise ValueError('terminal order fill state is immutable')
    c.execute('''INSERT INTO orders(id,client_order_id,decision_id,mode,market_id,side,requested_size,limit_price,status,filled_size,filled_notional,filled_fees,average_fill_price,venue_order_id,error,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,filled_size=EXCLUDED.filled_size,filled_notional=EXCLUDED.filled_notional,filled_fees=EXCLUDED.filled_fees,average_fill_price=EXCLUDED.average_fill_price,venue_order_id=EXCLUDED.venue_order_id,error=EXCLUDED.error,updated_at=EXCLUDED.updated_at''',(order.id,order.client_order_id,order.decision_id,order.mode.value,order.market_id,order.side,order.requested_size,order.limit_price,order.status.value,order.filled_size,order.filled_notional,order.filled_fees,order.average_fill_price,order.venue_order_id,order.error,order.created_at,order.updated_at))
    episode_row=c.execute('SELECT episode_id FROM eda_episodes WHERE decision_id=%s',(order.decision_id,)).fetchone()
    if episode_row:
      state_event_id=f'{order.id}:{order.status.value}:{order.updated_at}:{order.filled_size}:{order.filled_notional}:{order.filled_fees}'
      insert_canonical_event_json(c,self.db,make_canonical_event('execution','order_state',order.model_dump(mode='json'),episode_id=episode_row['episode_id'],source_event_id=state_event_id,event_time=order.updated_at))
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
 def append_eda_event(self,event):
  with self.lock:
   with self.db.connection() as c: insert_canonical_event_json(c,self.db,event)
  return event
 def episode(self,episode_id):
  with self.db.connection() as c:
   row=c.execute('SELECT episode FROM eda_episodes WHERE episode_id=%s',(episode_id,)).fetchone()
  return DecisionEpisode.model_validate(row['episode']) if row else None
 def episode_for_decision(self,decision_id):
  with self.db.connection() as c:
   row=c.execute('SELECT episode FROM eda_episodes WHERE decision_id=%s',(decision_id,)).fetchone()
  return DecisionEpisode.model_validate(row['episode']) if row else None
 def eda_events(self,episode_id=None,limit=200):
  with self.db.connection() as c:
   if episode_id:
    rows=c.execute('SELECT * FROM eda_events WHERE episode_id=%s ORDER BY event_time ASC LIMIT %s',(episode_id,max(1,min(int(limit),1000)))).fetchall()
   else:
    rows=c.execute('SELECT * FROM eda_events ORDER BY event_time DESC LIMIT %s',(max(1,min(int(limit),1000)),)).fetchall()
  return [dict(row) for row in rows]
 def eda_integrity(self):
  invalid_hashes=[]
  with self.db.connection() as c:
   episodes=c.execute('SELECT episode_id,episode,episode_hash FROM eda_episodes').fetchall()
   orphan_events=int(c.execute('SELECT COUNT(*) AS count FROM eda_events e LEFT JOIN eda_episodes p ON p.episode_id=e.episode_id WHERE e.episode_id IS NOT NULL AND p.episode_id IS NULL').fetchone()['count'])
   event_count=int(c.execute('SELECT COUNT(*) AS count FROM eda_events').fetchone()['count'])
  for row in episodes:
   try:
    if episode_hash(DecisionEpisode.model_validate(row['episode'])) != row['episode_hash']: invalid_hashes.append(row['episode_id'])
   except Exception: invalid_hashes.append(row['episode_id'])
  return {'episodes':len(episodes),'events':event_count,'orphan_events':orphan_events,'invalid_episode_hashes':invalid_hashes,'healthy':not invalid_hashes and orphan_events==0}
 def cleanup_retention(self):
  raw_days=max(1,int(os.getenv('RETENTION_RAW_MARKET_DAYS','30')));event_days=max(raw_days,int(os.getenv('RETENTION_MARKET_EVENT_DAYS',str(raw_days))));metric_days=max(1,int(os.getenv('RETENTION_METRIC_SAMPLE_DAYS','7')));journal_days=max(30,int(os.getenv('RETENTION_JOURNAL_DAYS','365')))
  with self.db.connection() as c:
   observations=c.execute("DELETE FROM market_observations WHERE observed_at < NOW()-(%s * interval '1 day')",(raw_days,)).rowcount
   snapshots=c.execute("DELETE FROM market_snapshots WHERE observed_at < NOW()-(%s * interval '1 day') AND NOT EXISTS (SELECT 1 FROM verified_market_inputs v WHERE v.snapshot_hash=market_snapshots.payload_hash)",(raw_days,)).rowcount
   events=c.execute("DELETE FROM eda_events WHERE event_type='market_snapshot' AND episode_id IS NULL AND event_time < NOW()-(%s * interval '1 day')",(event_days,)).rowcount
   samples=c.execute("DELETE FROM observability_metric_samples WHERE observed_at < NOW()-(%s * interval '1 day')",(metric_days,)).rowcount
   journal=c.execute("DELETE FROM journal WHERE created_at < NOW()-(%s * interval '1 day') AND event NOT IN ('decision','outcome_recorded','execution','execution_blocked','execution_reconciliation_required')",(journal_days,)).rowcount
  return {'observations':observations,'snapshots':snapshots,'market_events':events,'metric_samples':samples,'journal_events':journal}
 def save_opportunity(self,opportunity):
  with self.lock:
   with self.db.connection() as c:
    c.execute('''INSERT INTO eda_opportunities(opportunity_id,market_id,strategy_id,observed_at,status,reason,snapshot_hash,episode_id,payload,schema_version)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(opportunity_id) DO UPDATE SET status=EXCLUDED.status,reason=EXCLUDED.reason,episode_id=EXCLUDED.episode_id,payload=EXCLUDED.payload''',
      (opportunity.opportunity_id,opportunity.market_id,opportunity.strategy_id,opportunity.observed_at,opportunity.status,opportunity.reason,opportunity.snapshot_hash,opportunity.episode_id,self.db.json(opportunity.payload),opportunity.schema_version))
 def opportunities(self,limit=200):
  with self.db.connection() as c:return [dict(row) for row in c.execute('SELECT * FROM eda_opportunities ORDER BY observed_at DESC LIMIT %s',(max(1,min(int(limit),1000)),)).fetchall()]
 def save_replay(self,replay):
  with self.db.connection() as c:c.execute('INSERT INTO eda_replay_runs(replay_id,episode_id,mode,as_of,result) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(replay_id) DO UPDATE SET result=EXCLUDED.result',(replay.replay_id,replay.episode_id,replay.mode,replay.as_of,self.db.json(replay.result)))
 def replay_runs(self,episode_id=None,limit=100):
  with self.db.connection() as c:
   query='SELECT * FROM eda_replay_runs WHERE episode_id=%s ORDER BY created_at DESC LIMIT %s' if episode_id else 'SELECT * FROM eda_replay_runs ORDER BY created_at DESC LIMIT %s'
   params=(episode_id,max(1,min(int(limit),1000))) if episode_id else (max(1,min(int(limit),1000)),)
   return [dict(row) for row in c.execute(query,params).fetchall()]
 def save_model_registry(self,record):
  with self.db.connection() as c:
   if record.status=='active': c.execute("UPDATE eda_model_registry SET status='candidate',record=jsonb_set(record,'{status}','\"candidate\"'::jsonb) WHERE model_id=%s AND version<>%s",(record.model_id,record.version))
   c.execute('INSERT INTO eda_model_registry(model_id,version,status,record) VALUES(%s,%s,%s,%s) ON CONFLICT(model_id,version) DO UPDATE SET status=EXCLUDED.status,record=EXCLUDED.record',(record.model_id,record.version,record.status,self.db.json(record.model_dump(mode='json'))))
 def model_registry(self,limit=200):
  with self.db.connection() as c:return [dict(row['record']) for row in c.execute('SELECT record FROM eda_model_registry ORDER BY created_at DESC LIMIT %s',(max(1,min(int(limit),1000)),)).fetchall()]
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
