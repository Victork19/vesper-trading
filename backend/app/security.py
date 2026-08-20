import hashlib,secrets,threading,time
from collections import defaultdict,deque
from dataclasses import dataclass
from fastapi import HTTPException

@dataclass(frozen=True)
class Principal:
 key_id:str; scope:str

class SecurityManager:
 def __init__(self,settings):
  self.settings=settings;self.lock=threading.RLock();self.keys={};self.windows=defaultdict(deque);self.rate_limit= max(10,int(__import__('os').getenv('VESPER_RATE_LIMIT_PER_MINUTE','120')))
  self._add('client',settings.api_key,'trade');self._add('admin',settings.admin_key,'admin');self._add('reader',__import__('os').getenv('VESPER_READ_KEY',''),'read')
 def _hash(self,key):return hashlib.sha256(key.encode()).hexdigest()
 def _add(self,key_id,key,scope):
  if key:self.keys[self._hash(key)]=(key_id,scope)
 def authenticate(self,key,required='read'):
  if not self.settings.auth_required:return Principal('test', 'admin')
  if not key:raise HTTPException(401,'Missing X-Vesper-Key')
  record=self.keys.get(self._hash(key))
  if not record:raise HTTPException(401,'Invalid API key')
  key_id,scope=record;allowed=scope=='admin' or scope==required or (required=='read' and scope=='trade')
  if not allowed:raise HTTPException(403,'Insufficient API scope')
  return Principal(key_id,scope)
 def check_rate(self,key_id,route):
  now=time.time();bucket=self.windows[(key_id,route)]
  with self.lock:
   while bucket and now-bucket[0]>60:bucket.popleft()
   if len(bucket)>=self.rate_limit:raise HTTPException(429,'Rate limit exceeded; retry later',headers={'Retry-After':'60'})
   bucket.append(now)
 def rotate(self,scope='trade'):
  token='vesper_'+secrets.token_urlsafe(32);key_id='rotated_'+secrets.token_hex(4)
  with self.lock:self._add(key_id,token,scope)
  return {'key_id':key_id,'scope':scope,'key':token,'warning':'Store this key securely; it is returned once and is process-local until configured in the secret store.'}
 def revoke(self,key_id):
  with self.lock:
   for digest,(stored_id,_) in list(self.keys.items()):
    if stored_id==key_id:del self.keys[digest];return True
  return False
