import math, os, re
from datetime import datetime, timezone
import httpx

MODEL_VERSION='fast_market_v2'
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
def _fresh_candles(raw,max_age_seconds=180):
 timestamps=[];now=datetime.now(timezone.utc).timestamp()
 for row in raw if isinstance(raw,list) else []:
  try:
   value=float(row[0] if isinstance(row,list) else row.get('open_time',row.get('timestamp')));value=value/1000 if value>100000000000 else value
   timestamps.append(value)
  except (TypeError,ValueError,IndexError,KeyError):pass
 return bool(timestamps) and max(timestamps)<=now+60 and now-max(timestamps)<=max_age_seconds
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
 recent=returns[-min(10,len(returns)):];recent_mean=sum(recent)/len(recent);trend=max(-1,min(1,recent_mean/max(vol,.000001)))
 return {'probability':probability,'confidence':confidence,'momentum':max(0,min(1,.5+.5*math.tanh(trend/2))),'volatility':max(0,min(1,vol*100)),'observations':len(closes),'horizon_minutes':horizon,'model_version':MODEL_VERSION}

class FastMarketProbability:
 def __init__(self):
  self.enabled=os.getenv('FAST_MODEL_ENABLED','true').lower()=='true';self.client=httpx.Client(timeout=httpx.Timeout(5.0,connect=2.0),headers={'User-Agent':'vesper-fast-model/1.0'});self.cache={};self.cache_seconds=max(5,int(os.getenv('FAST_MODEL_CACHE_SECONDS','15')))
 def _history(self,symbol):
  now=datetime.now(timezone.utc).timestamp();cached=self.cache.get(symbol)
  if cached and now-cached[0]<self.cache_seconds:return cached[1]
  limit=max(20,min(120,int(os.getenv('FAST_MODEL_LOOKBACK_MINUTES','30'))));sources=[('binance',os.getenv('FAST_MODEL_MARKET_DATA_URL','https://api.binance.com/api/v3/klines'),{'symbol':symbol,'interval':'1m','limit':limit})]
  product=symbol.replace('USDT','-USD');sources.append(('coinbase',os.getenv('FAST_MODEL_FALLBACK_URL','https://api.exchange.coinbase.com/products')+'/'+product+'/candles',{'granularity':60,'limit':limit}))
  last=None
  for source,url,params in sources:
   try:
    response=self.client.get(url,params=params);response.raise_for_status();raw=response.json();closes=_closes(raw)
    if len(closes)>=12 and _fresh_candles(raw):self.cache[symbol]=(now,closes,source);return closes
   except (httpx.HTTPError,ValueError) as exc:last=exc
  raise RuntimeError(f'fast market data unavailable: {last}')
 def _calibrate(self,memory,raw_probability,model_version):
  if memory is None:return raw_probability
  samples=[]
  try:
   for decision in memory.decisions():
    if decision.model_version not in (model_version,MODEL_VERSION) or decision.outcome in ('pending','void') or decision.resolved_yes is None:continue
    predicted=float(decision.raw_model_probability if decision.raw_model_probability is not None else decision.fair_probability);actual=1.0 if decision.resolved_yes==(decision.side=='YES') else 0.0;samples.append((predicted,actual))
  except Exception:return raw_probability
  if len(samples)<int(os.getenv('FAST_MODEL_MIN_CALIBRATION_SAMPLES','20')):return raw_probability
  nearby=[actual for predicted,actual in samples if abs(predicted-raw_probability)<=.1]
  if not nearby:nearby=[actual for _,actual in samples]
  empirical=(sum(nearby)+5)/(len(nearby)+10);weight=min(.5,len(nearby)/(len(nearby)+50));return max(.05,min(.95,raw_probability*(1-weight)+empirical*weight))
 def estimate(self,item,market_input,memory=None):
  if not self.enabled:return None
  symbol=_asset(item.get('question'));direction=_direction(item.get('question'))
  if not symbol or not direction:return None
  try:closes=self._history(symbol)
  except (httpx.HTTPError,ValueError,RuntimeError):return None
  target=_number(item,'priceToBeat','price_to_beat','startPrice','start_price','strikePrice','strike_price','initialPrice','initial_price')
  estimate=estimate_from_closes(closes,market_input.resolution_hours*60,direction,target)
  if estimate is None or estimate['confidence']<float(os.getenv('FAST_MODEL_MIN_CONFIDENCE','.2')):return None
  raw=estimate['probability'];estimate['raw_probability']=raw;estimate['probability']=self._calibrate(memory,raw,estimate['model_version']);estimate['calibration_samples']=sum(1 for d in memory.decisions() if d.model_version in (estimate['model_version'],MODEL_VERSION) and d.outcome not in ('pending','void')) if memory is not None else 0
  return estimate|{'asset':symbol,'direction':direction}
