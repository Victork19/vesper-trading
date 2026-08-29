"""Short-horizon probability model for rapid Polymarket markets."""
import math, os, re
from datetime import datetime, timezone
import httpx
from .research_validation import is_research_eligible

MODEL_VERSION='fast_market_v3'
ASSETS={'BTC':'BTCUSDT','BITCOIN':'BTCUSDT','ETH':'ETHUSDT','ETHEREUM':'ETHUSDT','SOL':'SOLUSDT','SOLANA':'SOLUSDT','XRP':'XRPUSDT'}
def _normal_cdf(value): return .5*(1+math.erf(value/math.sqrt(2)))
def _clamp(value,lower=.05,upper=.95): return max(lower,min(upper,value))
def _asset(question):
 text=str(question or '').upper()
 for name,symbol in ASSETS.items():
  if re.search(rf'\b{re.escape(name)}\b',text): return symbol
 return None
def market_asset(question): return _asset(question)
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
   value=float(row[0] if isinstance(row,list) else row.get('open_time',row.get('timestamp')));value=value/1000 if value>100000000000 else value;timestamps.append(value)
  except (TypeError,ValueError,IndexError,KeyError):pass
 return bool(timestamps) and max(timestamps)<=now+60 and now-max(timestamps)<=max_age_seconds
def _returns(closes): return [math.log(closes[i]/closes[i-1]) for i in range(1,len(closes)) if closes[i]>0 and closes[i-1]>0]
def _winsorized(values,limit=4.0):
 if not values:return []
 mean=sum(values)/len(values);scale=math.sqrt(sum((x-mean)**2 for x in values)/max(1,len(values)-1)) or 1e-9
 return [max(mean-limit*scale,min(mean+limit*scale,x)) for x in values]
def _ewma(values,decay):
 if not values:return 0.0
 weight=1.0;total=normalizer=0.0
 for value in reversed(values): total+=weight*value;normalizer+=weight;weight*=decay
 return total/max(normalizer,1e-9)
def _variance(values):
 if len(values)<2:return 1e-8
 mean=sum(values)/len(values);return max(1e-12,sum((x-mean)**2 for x in values)/(len(values)-1))
def _median(values):
 if not values:return 0.0
 ordered=sorted(values);mid=len(ordered)//2
 return ordered[mid] if len(ordered)%2 else (ordered[mid-1]+ordered[mid])*.5
def _robust_scale(values):
 if len(values)<2:return 1e-6
 center=_median(values);mad=_median([abs(x-center) for x in values]);return max(1e-6,1.4826*mad)
def _lag1_autocorrelation(values):
 if len(values)<3:return 0.0
 mean=sum(values)/len(values);den=sum((x-mean)**2 for x in values)
 return 0.0 if den<=1e-12 else max(-.95,min(.95,sum((values[i]-mean)*(values[i-1]-mean) for i in range(1,len(values)))/den))
def _empirical_horizon_probability(closes,steps,threshold):
 steps=max(1,int(round(steps)));returns=[]
 for index in range(len(closes)-steps):
  start=closes[index];finish=closes[index+steps]
  if start>0 and finish>0:
   move=math.log(finish/start);returns.append(1.0 if move>threshold+1e-12 else 0.0 if move<threshold-1e-12 else .5)
 if not returns:return None,0.0
 weights=[.985**(len(returns)-1-index) for index in range(len(returns))];total=sum(weights);probability=sum(weight*value for weight,value in zip(weights,returns))/total
 effective=total*total/sum(weight*weight for weight in weights)
 return probability,effective

def estimate_from_closes(closes,horizon_minutes,direction='up',target_price=None):
 if len(closes)<20 or horizon_minutes<=0:return None
 returns=_winsorized(_returns(closes))
 if len(returns)<18:return None
 horizon=min(1440,max(1,float(horizon_minutes)));short=returns[-min(5,len(returns)):];medium=returns[-min(15,len(returns)):];long=returns[-min(60,len(returns)):]
 short_mean=_ewma(short,.72);medium_mean=_ewma(medium,.86);long_mean=_ewma(long,.94);horizon_weight=min(1.0,horizon/30.0);drift=(1-horizon_weight)*short_mean+horizon_weight*(.65*medium_mean+.35*long_mean)
 variance=_variance(long);standard_vol=math.sqrt(variance);robust_vol=_robust_scale(long);vol=.6*standard_vol+.4*robust_vol;recent_vol=math.sqrt(_variance(short));vol_ratio=recent_vol/max(vol,1e-8);autocorrelation=_lag1_autocorrelation(long);effective_observations=max(5,len(long)*(1-autocorrelation)/(1+autocorrelation));drift_shrink=min(1.0,math.sqrt(effective_observations/max(1,len(long))));trend_score=drift/max(vol,1e-8);mean_reversion=-((closes[-1]-sum(closes[-min(20,len(closes)):])/min(20,len(closes)))/max(closes[-1]*vol*math.sqrt(20),1e-8));stability=1/(1+max(0,vol_ratio-1)*1.5);effective_drift=drift*drift_shrink*(.7+.3*stability)+mean_reversion*vol*.08
 projected_mean=effective_drift*horizon*.42;projected_sigma=max(vol*math.sqrt(horizon),.0005)
 threshold=math.log(target_price/closes[-1]) if target_price is not None and target_price>0 else 0.0
 if target_price is not None and target_price>0: z=(threshold-projected_mean)/projected_sigma;p_parametric=1-_normal_cdf(z)
 else:p_parametric=_normal_cdf(projected_mean/projected_sigma)
 p_empirical,empirical_observations=_empirical_horizon_probability(closes,horizon,threshold);ensemble_weight=min(.35,empirical_observations/(empirical_observations+80)) if p_empirical is not None else 0.0;p_up=(1-ensemble_weight)*p_parametric+ensemble_weight*p_empirical
 disagreement=abs(p_parametric-p_empirical) if p_empirical is not None else .15
 probability=_clamp(p_up if direction=='up' else 1-p_up);uncertainty=min(.48,.10+1/math.sqrt(max(1,effective_observations))+.08*abs(autocorrelation)+.10*disagreement+abs(vol_ratio-1)*.06+max(0,1-stability)*.08+(1.96*math.sqrt(max(.001,p_empirical*(1-p_empirical))/max(1,empirical_observations)) if p_empirical is not None else .08));lower=_clamp(probability-uncertainty,.01,.99);upper=_clamp(probability+uncertainty,.01,.99);confidence=_clamp(.9-uncertainty*1.5,.05,.85)
 if upper-lower>.65:confidence=min(confidence,.3)
 regime='shock' if vol_ratio>=2 else 'high_volatility' if vol_ratio>=1.35 else 'trend' if abs(trend_score)>=.8 else 'mean_reversion' if abs(mean_reversion)>=.8 else 'range'
 return {'probability':probability,'confidence':confidence,'lower_bound':lower,'upper_bound':upper,'uncertainty':uncertainty,'momentum':_clamp(.5+.5*math.tanh(trend_score/2),0,1),'volatility':_clamp(vol*100,0,1),'volatility_ratio':vol_ratio,'autocorrelation':autocorrelation,'effective_observations':effective_observations,'empirical_observations':empirical_observations,'ensemble_weight':ensemble_weight,'parametric_probability':p_parametric,'empirical_probability':p_empirical,'model_disagreement':disagreement,'trend_score':trend_score,'regime':regime,'observations':len(closes),'horizon_minutes':horizon,'model_version':MODEL_VERSION}

class FastMarketProbability:
 def __init__(self): self.enabled=os.getenv('FAST_MODEL_ENABLED','true').lower()=='true';self.client=httpx.Client(timeout=httpx.Timeout(5.0,connect=2.0),headers={'User-Agent':'vesper-fast-model/1.0'});self.cache={};self.cache_seconds=max(5,int(os.getenv('FAST_MODEL_CACHE_SECONDS','15')))
 def close(self):self.client.close()
 def _history(self,symbol):
  now=datetime.now(timezone.utc).timestamp();cached=self.cache.get(symbol)
  if cached and now-cached[0]<self.cache_seconds:return cached[1]
  limit=max(20,min(120,int(os.getenv('FAST_MODEL_LOOKBACK_MINUTES','30'))));sources=[('binance',os.getenv('FAST_MODEL_MARKET_DATA_URL','https://api.binance.com/api/v3/klines'),{'symbol':symbol,'interval':'1m','limit':limit})];product=symbol.replace('USDT','-USD');sources.append(('coinbase',os.getenv('FAST_MODEL_FALLBACK_URL','https://api.exchange.coinbase.com/products')+'/'+product+'/candles',{'granularity':60,'limit':limit}));last=None
  for source,url,params in sources:
   try:
    response=self.client.get(url,params=params);response.raise_for_status();raw=response.json();closes=_closes(raw)
    if len(closes)>=20 and _fresh_candles(raw):self.cache[symbol]=(now,closes,source);return closes
   except (httpx.HTTPError,ValueError) as exc:last=exc
  raise RuntimeError(f'fast market data unavailable: {last}')
 def _calibration_samples(self,memory,model_version,as_of=None):
  samples=[]
  if memory is None:return samples
  cutoff=as_of or datetime.now(timezone.utc)
  try: decisions=memory.decisions()
  except Exception as exc: raise RuntimeError('calibration data unavailable') from exc
  for decision in decisions:
    # Calibration must use the same immutable, execution-backed population as
    # research.  A resolved decision with a hand-entered outcome or no verified
    # quote is not valid training evidence.
    if decision.model_version not in (model_version,MODEL_VERSION) or not is_research_eligible(decision,as_of=cutoff):continue
    try:
     created=datetime.fromisoformat(str(decision.created_at).replace('Z','+00:00'))
     resolved_at=datetime.fromisoformat(str(decision.resolved_at).replace('Z','+00:00')) if decision.resolved_at else None
     if resolved_at is None or created>=resolved_at or created>=cutoff or resolved_at>cutoff:continue
    except (TypeError,ValueError):
     continue
    predicted=float(decision.raw_model_probability if decision.raw_model_probability is not None else decision.fair_probability);samples.append((max(.001,min(.999,predicted)),1.0 if decision.resolved_yes else 0.0,decision.regime))
  return samples
 def _calibrate(self,memory,raw_probability,model_version,as_of=None):
  samples=self._calibration_samples(memory,model_version,as_of);minimum=int(os.getenv('FAST_MODEL_MIN_CALIBRATION_SAMPLES','20'))
  return self._calibrate_values(samples,raw_probability,minimum)
 def _calibrate_values(self,samples,raw_probability,minimum):
  if not samples:return raw_probability,.20
  nearby=[actual for predicted,actual,_ in samples if abs(predicted-raw_probability)<=.12];nearby=nearby if len(nearby)>=5 else [actual for _,actual,_ in samples];count=len(nearby);wins=sum(nearby);posterior=(wins+1)/(count+2);posterior_sd=math.sqrt(max(.0001,posterior*(1-posterior)/(count+3)));weight=min(.55,count/(count+60));calibrated=_clamp(raw_probability*(1-weight)+posterior*weight);uncertainty=min(.30,1.96*posterior_sd+(0.08 if count<minimum else 0));return calibrated,uncertainty
 def estimate(self,item,market_input,memory=None):
  if not self.enabled:return None
  symbol=_asset(item.get('question'));direction=_direction(item.get('question'))
  if not symbol or not direction:return None
  try:closes=self._history(symbol)
  except (httpx.HTTPError,ValueError,RuntimeError):return None
  target=_number(item,'priceToBeat','price_to_beat','startPrice','start_price','strikePrice','strike_price','initialPrice','initial_price');estimate=estimate_from_closes(closes,market_input.resolution_hours*60,direction,target)
  if estimate is None or estimate['confidence']<float(os.getenv('FAST_MODEL_MIN_CONFIDENCE','.2')):return None
  raw=estimate['probability'];as_of=market_input.observed_at or datetime.now(timezone.utc);samples=self._calibration_samples(memory,estimate['model_version'],as_of);regime_samples=[sample for sample in samples if sample[2]==estimate['regime']];calibration_samples=regime_samples if len(regime_samples)>=5 else samples;calibrated,calibration_uncertainty=self._calibrate_values(calibration_samples,raw,int(os.getenv('FAST_MODEL_MIN_CALIBRATION_SAMPLES','20')))
  estimate['raw_probability']=raw;estimate['probability']=calibrated;estimate['calibration_samples']=len(calibration_samples);estimate['calibration_uncertainty']=calibration_uncertainty;estimate['uncertainty']=min(.48,estimate['uncertainty']+calibration_uncertainty);estimate['calibration_status']='usable' if len(calibration_samples)>=int(os.getenv('FAST_MODEL_MIN_CALIBRATION_SAMPLES','20')) else 'warming';estimate['lower_bound']=_clamp(calibrated-estimate['uncertainty'],.01,.99);estimate['upper_bound']=_clamp(calibrated+estimate['uncertainty'],.01,.99)
  return estimate|{'asset':symbol,'direction':direction}
