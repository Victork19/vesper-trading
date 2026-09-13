from __future__ import annotations
import json, os
from datetime import datetime, timezone
from .db import PostgresDatabase

CONFIG_SCHEMA = {
 'PIPELINE_INTERVAL_SECONDS': {'label':'Pipeline interval', 'description':'Seconds between ingestion cycles.', 'type':'number', 'default':60, 'min':5, 'max':3600, 'group':'Worker'},
 'PIPELINE_MAX_BACKOFF_SECONDS': {'label':'Maximum retry backoff', 'description':'Maximum delay after an external or database failure.', 'type':'number', 'default':900, 'min':30, 'max':86400, 'group':'Worker'},
 'AUTO_PAPER_ENABLED': {'label':'Autonomous paper loop', 'description':'Evaluate eligible markets automatically in paper mode.', 'type':'boolean', 'default':True, 'group':'Research'},
 'AUTO_PAPER_DECISIONS_PER_TICK': {'label':'Decisions per tick', 'description':'Maximum autonomous evaluations per ingestion cycle.', 'type':'number', 'default':3, 'min':1, 'max':50, 'group':'Research'},
 'AUTO_PAPER_EXPLORATION_ENABLED': {'label':'Paper exploration enabled', 'description':'Place tiny exploratory paper positions separately from research evidence.', 'type':'boolean', 'default':True, 'group':'Research'},
 'AUTO_PAPER_EXPLORATION_MAX_PER_TICK': {'label':'Exploration trades per tick', 'description':'Maximum fixed-size exploration positions per ingestion cycle.', 'type':'number', 'default':2, 'min':0, 'max':20, 'group':'Research'},
 'AUTO_PAPER_EXPLORATION_SIZE': {'label':'Exploration position size', 'description':'Maximum contracts per exploration position; never used for live readiness.', 'type':'number', 'default':0.01, 'min':0.0001, 'max':0.01, 'group':'Research'},
 'AUTO_PAPER_MARKET_COOLDOWN_SECONDS': {'label':'Market cooldown', 'description':'Minimum seconds before reevaluating the same market.', 'type':'number', 'default':21600, 'min':60, 'max':604800, 'group':'Research'},
 'AUTO_PAPER_MAX_PER_TYPE_PER_TICK': {'label':'Max per market type', 'description':'Prevents one asset/category from consuming a full tick.', 'type':'number', 'default':1, 'min':1, 'max':20, 'group':'Research'},
 'AUTO_PAPER_MIN_RESOLUTION_HOURS': {'label':'Minimum resolution horizon', 'description':'Exclude markets resolving sooner than this horizon.', 'type':'number', 'default':0.05, 'min':0.01, 'max':168, 'group':'Market selection'},
 'AUTO_PAPER_MAX_RESOLUTION_HOURS': {'label':'Maximum resolution horizon', 'description':'General maximum resolution horizon for paper research.', 'type':'number', 'default':24, 'min':0.05, 'max':720, 'group':'Market selection'},
 'AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS': {'label':'Fast-market horizon', 'description':'Maximum horizon for BTC/ETH and other fast markets.', 'type':'number', 'default':0.25, 'min':0.05, 'max':24, 'group':'Market selection'},
 'AUTO_PAPER_PREFER_FAST_MARKETS': {'label':'Prefer fast markets', 'description':'Prioritize short-horizon markets when discovery returns mixed results.', 'type':'boolean', 'default':True, 'group':'Market selection'},
 'FAST_MARKETS_ONLY': {'label':'Fast markets only', 'description':'Restrict autonomous research to recognized fast markets.', 'type':'boolean', 'default':True, 'group':'Market selection'},
 'MAX_PORTFOLIO_HEAT': {'label':'Portfolio heat cap', 'description':'Maximum paper exposure fraction across the portfolio.', 'type':'number', 'default':0.20, 'min':0.01, 'max':0.50, 'group':'Risk'},
 'MAX_CORRELATED_EXPOSURE': {'label':'Correlation cluster cap', 'description':'Maximum exposure in one correlated asset cluster.', 'type':'number', 'default':0.12, 'min':0.01, 'max':0.50, 'group':'Risk'},
 'MAX_MARKET_EXPOSURE': {'label':'Market exposure cap', 'description':'Maximum exposure allocated to one market.', 'type':'number', 'default':0.05, 'min':0.001, 'max':0.25, 'group':'Risk'},
 'PAPER_QUEUE_FILL_FACTOR': {'label':'Paper queue fill factor', 'description':'Conservative fraction of displayed size assumed filled.', 'type':'number', 'default':0.85, 'min':0.05, 'max':1, 'group':'Execution assumptions'},
 'PAPER_LATENCY_SLIPPAGE_BPS': {'label':'Paper latency slippage', 'description':'Synthetic latency slippage used in paper fills.', 'type':'number', 'default':5, 'min':0, 'max':100, 'group':'Execution assumptions'},
 'AUTO_PAPER_STRATEGY': {'label':'Autonomous strategy', 'description':'Strategy used for autonomous paper decisions.', 'type':'select', 'default':'reference_class', 'options':['reference_class','relative_microstructure'], 'group':'Strategy'},
 'EXPERIMENTAL_STRATEGY_ENABLED': {'label':'Experimental strategy enabled', 'description':'Allows the experimental strategy to be selected; it remains evidence-gated.', 'type':'boolean', 'default':False, 'group':'Strategy'},
}

def _now(): return datetime.now(timezone.utc)
class RuntimeConfig:
 def __init__(self, database=None): self.db=database or PostgresDatabase()
 def _defaults(self):
  result={}
  for key,spec in CONFIG_SCHEMA.items():
   raw=os.getenv(key)
   if raw is None: result[key]=spec['default']; continue
   if spec['type']=='boolean': result[key]=raw.lower()=='true'
   elif spec['type']=='number': result[key]=float(raw) if '.' in raw else int(raw)
   else: result[key]=raw
  return result
 def get_all(self):
  result=self._defaults()
  with self.db.connection() as c:
   for row in c.execute('SELECT key,value FROM runtime_config').fetchall(): result[row['key']]=row['value']
  return result
 def history(self,limit=20):
  with self.db.connection() as c:return c.execute('SELECT version,changes,previous,updated_by,reason,updated_at FROM runtime_config_history ORDER BY version DESC LIMIT %s',(max(1,min(int(limit),100)),)).fetchall()
 def _validate(self,key,value):
  spec=CONFIG_SCHEMA.get(key)
  if not spec: raise ValueError(f'Unsupported runtime setting: {key}')
  if spec['type']=='boolean' and not isinstance(value,bool): raise ValueError(f'{key} must be boolean')
  if spec['type'] in ('number',) and (isinstance(value,bool) or not isinstance(value,(int,float))): raise ValueError(f'{key} must be numeric')
  if spec['type']=='select' and value not in spec['options']: raise ValueError(f'{key} must be one of {spec["options"]}')
  if 'min' in spec and value<spec['min'] or 'max' in spec and value>spec['max']: raise ValueError(f'{key} is outside its safe range')
  if key=='AUTO_PAPER_MAX_RESOLUTION_HOURS' and value<self.get_all()['AUTO_PAPER_MIN_RESOLUTION_HOURS']: raise ValueError('Maximum horizon cannot be below minimum horizon')
  return value
 def update(self,changes,actor,reason='operator update'):
  if not isinstance(changes,dict) or not changes: raise ValueError('At least one runtime setting is required')
  current=self.get_all(); validated={key:self._validate(key,value) for key,value in changes.items()}; previous={key:current.get(key) for key in validated}
  now=_now()
  with self.db.connection() as c:
   row=c.execute('INSERT INTO runtime_config_history(changes,previous,updated_by,reason,updated_at) VALUES (%s,%s,%s,%s,%s) RETURNING version',(self.db.json(validated),self.db.json(previous),actor,reason[:240],now)).fetchone(); version=row['version']
   for key,value in validated.items(): c.execute('INSERT INTO runtime_config(key,value,version,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,version=EXCLUDED.version,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at',(key,self.db.json(value),version,actor,now))
  self.sync_process(validated); return {'version':version,'values':self.get_all()}
 def rollback(self,version,actor):
  with self.db.connection() as c: row=c.execute('SELECT previous FROM runtime_config_history WHERE version=%s',(int(version),)).fetchone()
  if not row: raise ValueError('Configuration version not found')
  return self.update(row['previous'],actor,f'rollback to version {version}')
 def sync_process(self,values=None):
  values=values or self.get_all()
  for key,value in values.items():
   if isinstance(value,bool): os.environ[key]='true' if value else 'false'
   else: os.environ[key]=str(value)
  return values
 def payload(self):
  values=self.get_all(); return {'values':values,'schema':CONFIG_SCHEMA,'history':self.history()}
