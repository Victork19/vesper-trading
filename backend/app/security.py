import base64,hashlib,hmac,json,os,secrets,threading,time
from collections import defaultdict,deque
from dataclasses import dataclass
from fastapi import HTTPException

@dataclass(frozen=True)
class Principal:
 key_id:str; scope:str

class SecurityManager:
 def __init__(self,settings,database=None):
  self.settings=settings;self.db=database;self.lock=threading.RLock();self.keys={};self.windows=defaultdict(deque);self.rate_limit=max(10,int(os.getenv('VESPER_RATE_LIMIT_PER_MINUTE','120')))
  self._add('client',settings.api_key,'trade');self._add('admin',settings.admin_key,'admin');self._add('reader',os.getenv('VESPER_READ_KEY',''),'read');self._load_persisted_keys()
 def _hash(self,key):return hashlib.sha256(key.encode()).hexdigest()
 def _add(self,key_id,key,scope):
  if not key:return
  digest=self._hash(key)
  if self.db:
   # Environment keys are durable identities. A changed secret replaces the
   # previous fixed identity; the exact same revoked secret remains revoked.
   with self.db.connection() as c:
    existing=c.execute('SELECT key_digest,active FROM security_keys WHERE key_id=%s',(key_id,)).fetchone()
    collision=c.execute('SELECT key_id FROM security_keys WHERE key_digest=%s AND key_id<>%s',(digest,key_id)).fetchone()
    if collision: raise RuntimeError(f'Key digest is already owned by {collision["key_id"]}; refusing identity collision.')
    if existing and existing['key_digest']!=digest:
     c.execute('UPDATE security_keys SET key_digest=%s,scope=%s,active=TRUE,created_at=now(),revoked_at=NULL WHERE key_id=%s',(digest,scope,key_id))
    elif not existing:
     c.execute('INSERT INTO security_keys(key_digest,key_id,scope,active,created_at) VALUES(%s,%s,%s,TRUE,now()) ON CONFLICT(key_digest) DO NOTHING',(digest,key_id,scope))
  self.keys[digest]=(key_id,scope)
 def _load_persisted_keys(self):
  if not self.db:return
  with self.db.connection() as c:
   for row in c.execute('SELECT key_digest,key_id,scope FROM security_keys WHERE active=TRUE').fetchall():self.keys[row['key_digest']]=(row['key_id'],row['scope'])
 def _record(self,key):
  digest=self._hash(key)
  if self.db:
   with self.db.connection() as c:
    row=c.execute('SELECT key_id,scope FROM security_keys WHERE key_digest=%s AND active=TRUE',(digest,)).fetchone()
    return (row['key_id'],row['scope']) if row else None
  return self.keys.get(digest)
 def _active_key_id(self,key_id):
  if not self.db:return any(stored_id==key_id for stored_id,_ in self.keys.values())
  with self.db.connection() as c:return c.execute('SELECT 1 FROM security_keys WHERE key_id=%s AND active=TRUE',(key_id,)).fetchone() is not None
 def authenticate(self,key,required='read'):
  if not self.settings.auth_required:return Principal('test', 'admin')
  if not key:raise HTTPException(401,'Missing X-Vesper-Key')
  record=self._record(key)
  if not record:raise HTTPException(401,'Invalid API key')
  key_id,scope=record;allowed=scope=='admin' or scope==required or (required=='read' and scope=='trade')
  if not allowed:raise HTTPException(403,'Insufficient API scope')
  return Principal(key_id,scope)
 def check_rate(self,key_id,route):
  if self.db:
   with self.db.connection() as c:
    count=c.execute("INSERT INTO security_rate_limits(principal,route,window_start,request_count) VALUES(%s,%s,now(),1) ON CONFLICT(principal,route) DO UPDATE SET request_count=CASE WHEN security_rate_limits.window_start < now()-interval '60 seconds' THEN 1 ELSE security_rate_limits.request_count+1 END,window_start=CASE WHEN security_rate_limits.window_start < now()-interval '60 seconds' THEN now() ELSE security_rate_limits.window_start END RETURNING request_count",(key_id,route)).fetchone()['request_count']
   if count>self.rate_limit:raise HTTPException(429,'Rate limit exceeded; retry later',headers={'Retry-After':'60'})
   return
  now=time.time();bucket=self.windows[(key_id,route)]
  with self.lock:
   while bucket and now-bucket[0]>60:bucket.popleft()
   if len(bucket)>=self.rate_limit:raise HTTPException(429,'Rate limit exceeded; retry later',headers={'Retry-After':'60'})
   bucket.append(now)
 def rotate(self,scope='trade'):
  token='vesper_'+secrets.token_urlsafe(32);key_id='rotated_'+secrets.token_hex(4)
  with self.lock:self._add(key_id,token,scope)
  return {'key_id':key_id,'scope':scope,'key':token,'warning':'Store this key securely; it is returned once. The key identity is persisted, but the secret cannot be recovered from the database.'}
 def revoke(self,key_id):
  if self.db:
   with self.db.connection() as c:changed=c.execute('UPDATE security_keys SET active=FALSE,revoked_at=now() WHERE key_id=%s AND active=TRUE',(key_id,)).rowcount
   if changed:
    with self.lock:
     for digest,(stored_id,_) in list(self.keys.items()):
      if stored_id==key_id:del self.keys[digest]
    return True
   return False
  with self.lock:
   for digest,(stored_id,_) in list(self.keys.items()):
    if stored_id==key_id:del self.keys[digest];return True
  return False
 def create_session(self,key,ttl=None):
  principal=self.authenticate(key,'read')
  secret=self.settings.session_secret
  if not secret: raise HTTPException(503,'Session authentication is not configured')
  expires=int(time.time())+int(ttl or self.settings.session_ttl_seconds)
  payload={'sid':'sess_'+secrets.token_urlsafe(18),'key_id':principal.key_id,'scope':principal.scope,'exp':expires}
  encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
  signature=hmac.new(secret.encode(),encoded.encode(),hashlib.sha256).digest()
  return encoded+'.'+base64.urlsafe_b64encode(signature).decode().rstrip('='),payload
 def authenticate_session(self,token,required='read'):
  if not token: raise HTTPException(401,'Missing session')
  secret=self.settings.session_secret
  if not secret: raise HTTPException(503,'Session authentication is not configured')
  try:
   encoded,provided=token.split('.',1)
   expected=base64.urlsafe_b64encode(hmac.new(secret.encode(),encoded.encode(),hashlib.sha256).digest()).decode().rstrip('=')
   if not hmac.compare_digest(provided,expected): raise ValueError('signature')
   payload=json.loads(base64.urlsafe_b64decode(encoded+'='*((4-len(encoded)%4)%4)))
   if int(payload.get('exp',0))<int(time.time()): raise ValueError('expired')
   scope=str(payload.get('scope',''));key_id=str(payload.get('key_id','session'))
   if not self._active_key_id(key_id): raise HTTPException(401,'Session key has been revoked')
   if not (scope=='admin' or scope==required or (required=='read' and scope=='trade')): raise HTTPException(403,'Insufficient session scope')
   return Principal(key_id,scope)
  except HTTPException: raise
  except Exception: raise HTTPException(401,'Invalid or expired session')
