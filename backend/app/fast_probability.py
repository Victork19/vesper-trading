import math, os, re
from datetime import datetime, timezone
import httpx

MODEL_VERSION='fast_market_v1'
ASSETS={'BTC':'BTCUSDT','BITCOIN':'BTCUSDT','ETH':'ETHUSDT','ETHEREUM':'ETHUSDT','SOL':'SOLUSDT','SOLANA':'SOLUSDT'}

def _normal_cdf(value): return .5*(1+math.erf(value/math.sqrt(2)))
def _asset(question):
 text=str(question or '').upper()
 for name,symbol in ASSETS.items():
  if re.search(rf'\b{re.escape(name)}\b',text): return symbol
 return None
def _direction(question):
 text=str(question or '').lower()
 if any(x in text for x in ('up or down','higher or lower','up/down','higher/lower')): return 'up' if 'up' in text or 'higher' in text else 'down'
 if re.search(r'\b(up|above|increase|higher|rise|rises|positive)\b',text): return 'up'
 if re.search(r'\b(down|below|decrease|lower|fall|falls|negative)\b',text): return 'down'
 return None
def _number(item,*keys):
 for key in keys:
  try:
   value=item.get(key)
   if value not in (None,''): return float(value)
  except (TypeError,ValueError): pass
 return None
def _closes(raw):
 values=[]
 for row in raw if isinstance(raw,list) else []:
  try:
   value=float(row[4] if isinstance(row,list) else row.get('close'))
   if value>0: values.append(value)
  except (TypeError,ValueError,IndexError,KeyError): pass
 return values
def estimate_from_closes(closes,horizon_minutes,direction='up',target_price=None):
 if len(closes)<12 or horizon_minutes<=0:return None
 returns=[math.log(closes[index]/closes[index-1]) for index in range(1,len(closes)) if closes[index]>0 and closes[index-1]>0]
 if len(returns)<10:return None
 mean=sum(returns)/len(returns);variance=sum((value-mean)**2 for value in returns)/max(1,len(returns)-1);vol=math.sqrt(max(variance,1e-12))
 horizon=min(1440,max(1,float(horizon_minutes)));projected_mean=mean*horizon*.35;projected_sigma=max(vol*math.sqrt(horizon),.0005)
 if target_price is not None and target_price>0:
  z=(math.log(target_price/closes[-1])-projected_mean)/projected_sigma;p_up=1-_normal_cdf(z)
 else:p_up=.5+.5*math.tanh(projected_mean/projected_sigma)
 probability=max(.05,min(.95,p_up if direction=='up' else 1-p_up));confidence=max(.05,min(.85,.15+abs(probability-.5)*2*.7))
 return {'probability':probability,'confidence':confidence,'momentum':max(0,min(1,.5+.5*math.tanh(projected_mean/.005))),'volatility':max(0,min(1,vol*100)),'observations':len(closes),'horizon_minutes':horizon,'model_version':MODEL_VERSION}

class FastMarketProbability:
 def __init__(self):
  self.enabled=os.getenv('FAST_MODEL_ENABLED','true').lower()=='true';self.client=httpx.Client(timeout=httpx.Timeout(5.0,connect=2.0),headers={'User-Agent':'vesper-fast-model/1.0'});self.cache={};self.cache_seconds=max(5,int(os.getenv('FAST_MODEL_CACHE_SECONDS','15')))
 def _history(self,symbol):
  now=datetime.now(timezone.utc).timestamp();cached=self.cache.get(symbol)
  if cached and now-cached[0]<self.cache_seconds:return cached[1]
  response=self.client.get(os.getenv('FAST_MODEL_MARKET_DATA_URL','https://api.binance.com/api/v3/klines'),params={'symbol':symbol,'interval':'1m','limit':max(20,min(120,int(os.getenv('FAST_MODEL_LOOKBACK_MINUTES','30'))))});response.raise_for_status();closes=_closes(response.json());self.cache[symbol]=(now,closes);return closes
 def estimate(self,item,market_input):
  if not self.enabled:return None
  symbol=_asset(item.get('question'));direction=_direction(item.get('question'))
  if not symbol or not direction:return None
  try:closes=self._history(symbol)
  except (httpx.HTTPError,ValueError):return None
  target=_number(item,'priceToBeat','price_to_beat','startPrice','start_price','strikePrice','strike_price','initialPrice','initial_price')
  estimate=estimate_from_closes(closes,market_input.resolution_hours*60,direction,target)
  if estimate is None or estimate['confidence']<float(os.getenv('FAST_MODEL_MIN_CONFIDENCE','.2')):return None
  return estimate|{'asset':symbol,'direction':direction}
