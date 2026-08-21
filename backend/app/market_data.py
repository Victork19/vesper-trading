import hashlib,json, os, time
from datetime import datetime, timezone
import httpx
from .models import MarketInput, OrderBook, BookLevel, MarketQuality, now_iso
from .observability import telemetry

GAMMA=os.getenv('POLYMARKET_GAMMA_URL','https://gamma-api.polymarket.com')
CLOB=os.getenv('POLYMARKET_CLOB_URL','https://clob.polymarket.com')
INPUT_SCHEMA_VERSION='market_input_v2'

class MarketDataError(RuntimeError): pass

class PolymarketData:
 def __init__(self):
  self.client=httpx.Client(timeout=httpx.Timeout(10.0,connect=3.0),headers={'User-Agent':'vesper-trading/7.0'})
  self.retries=max(1,int(os.getenv('MARKET_DATA_RETRIES','3')));self.max_book_levels=max(1,int(os.getenv('MAX_BOOK_LEVELS','50')))
 def close(self):self.client.close()
 def _get(self,url,params=None):
  last=None
  for attempt in range(self.retries):
   try:
    response=self.client.get(url,params=params);response.raise_for_status();return response.json()
   except (httpx.HTTPError,ValueError) as exc:
    last=exc
    if attempt+1<self.retries:time.sleep(.2*(2**attempt))
  telemetry.error('market_data_request');raise MarketDataError(f'market data request failed after {self.retries} attempts: {last}')
 def markets(self,limit=20,active=True,offset=0,order=None,ascending=None,closed=None,end_date_min=None,end_date_max=None):
  params={'limit':max(1,min(int(limit),100)),'offset':max(0,int(offset)),'active':str(active).lower()}
  if order:params['order']=str(order)
  if ascending is not None:params['ascending']=str(bool(ascending)).lower()
  if closed is not None:params['closed']=str(bool(closed)).lower()
  if end_date_min is not None:params['end_date_min']=str(end_date_min)
  if end_date_max is not None:params['end_date_max']=str(end_date_max)
  data=self._get(GAMMA+'/markets',params)
  if not isinstance(data,list):raise MarketDataError('Gamma markets response was not a list')
  return [self.validate_market(x) for x in data if isinstance(x,dict)]
 def market(self,market_id):
  data=self._get(GAMMA+'/markets/'+str(market_id))
  if not isinstance(data,dict):raise MarketDataError('Gamma market response was not an object')
  return self.validate_market(data)
 def book(self,token_id):
  raw=self._get(CLOB+'/book',{'token_id':str(token_id)})
  if not isinstance(raw,dict):raise MarketDataError('CLOB book response was not an object')
  def levels(name,reverse):
   aggregated={}
   for row in raw.get(name,[]):
    try:
     price=float(row.get('price') if isinstance(row,dict) else row[0]);size=float(row.get('size') if isinstance(row,dict) else row[1])
     if 0<=price<=1 and size>0:aggregated[price]=aggregated.get(price,0)+size
    except (TypeError,ValueError,IndexError,KeyError):continue
   return sorted([BookLevel(price=p,size=s) for p,s in aggregated.items()],key=lambda x:x.price,reverse=reverse)[:self.max_book_levels]
  bids=levels('bids',True);asks=levels('asks',False)
  if bids and asks and bids[0].price>=asks[0].price:telemetry.error('crossed_order_book');raise MarketDataError('crossed or locked order book')
  telemetry.inc('vesper_books_fetched_total');telemetry.set('vesper_book_bid_depth',sum(x.size for x in bids));telemetry.set('vesper_book_ask_depth',sum(x.size for x in asks))
  return OrderBook(token_id=str(token_id),observed_at=self._observed_at(raw),bids=bids,asks=asks,best_bid=bids[0].price if bids else None,best_ask=asks[0].price if asks else None,bid_depth=sum(x.size for x in bids),ask_depth=sum(x.size for x in asks),sequence=self._sequence(raw))
 def _observed_at(self,raw):
  value=next((raw.get(key) for key in ('timestamp','updatedAt','updated_at','lastUpdated','last_update') if raw.get(key) not in (None,'')),None)
  if value is None:return now_iso()
  try:
   try:timestamp=float(value)
   except (TypeError,ValueError):timestamp=None
   if timestamp is not None:
    timestamp=timestamp/1000 if timestamp>100000000000 else timestamp
    return datetime.fromtimestamp(timestamp,timezone.utc).isoformat().replace('+00:00','Z')
   return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
  except (TypeError,ValueError,OSError):return now_iso()
 def _sequence(self,raw):
  value=raw.get('sequence') or raw.get('timestamp')
  try:return int(value) if value is not None else None
  except (TypeError,ValueError):return None
 def validate_market(self,item):
  market=dict(item);market_id=str(market.get('id') or market.get('conditionId') or '')
  if not market_id:raise MarketDataError('market has no stable identifier')
  market['_vesper_validated_at']=now_iso();market['_vesper_market_id']=market_id;return market
 def _number(self,value,default=0):
  try:return float(value) if value not in (None,'') else default
  except (TypeError,ValueError):return default
 def _end_time(self,item):
  value=item.get('endDate') or item.get('end_date') or item.get('endTime')
  if not value:return None
  try:return datetime.fromisoformat(str(value).replace('Z','+00:00'))
  except (TypeError,ValueError):return None
 def token_pair(self,item):
  tokens=item.get('clobTokenIds') or item.get('clobTokenIDs') or []
  outcomes=item.get('outcomes',[])
  if isinstance(tokens,str):
   try:tokens=json.loads(tokens)
   except json.JSONDecodeError:tokens=[]
  if isinstance(outcomes,str):
   try:outcomes=json.loads(outcomes)
   except json.JSONDecodeError:outcomes=[]
  if not isinstance(tokens,list):return None,None
  yes=no=None
  if isinstance(outcomes,list) and len(outcomes)==len(tokens):
   for token,label in zip(tokens,outcomes):
    text=str(label).strip().lower()
    if text in ('yes','true'):yes=str(token)
    elif text in ('no','false'):no=str(token)
  if yes is None and tokens:yes=str(tokens[0])
  if no is None and len(tokens)>1:no=str(tokens[1])
  return yes,no
 def to_input(self,item,book=None,yes_book=None,no_book=None):
  item=self.validate_market(item);yes_book=yes_book or book;yes_token,no_token=self.token_pair(item);prices=item.get('outcomePrices',[.5])
  if isinstance(prices,str):
   try:prices=json.loads(prices)
   except json.JSONDecodeError:prices=[.5]
  price=max(0,min(1,self._number(prices[0] if isinstance(prices,list) and prices else item.get('lastTradePrice',.5),.5)));yes_bid=yes_ask=no_bid=no_ask=None
  if yes_book is not None:
   yes_bid,yes_ask=yes_book.best_bid,yes_book.best_ask
   if yes_bid is not None:no_ask=max(0,min(1,1-yes_bid))
   if yes_ask is not None:no_bid=max(0,min(1,1-yes_ask))
  if no_book is not None:no_bid,no_ask=no_book.best_bid,no_book.best_ask
  observed=datetime.now(timezone.utc);quote_book=yes_book or no_book;yes_observed=datetime.fromisoformat(yes_book.observed_at.replace('Z','+00:00')) if yes_book else None;no_observed=datetime.fromisoformat(no_book.observed_at.replace('Z','+00:00')) if no_book else None;quote_times=[value for value in (yes_observed,no_observed) if value is not None];quote_observed=min(quote_times) if quote_times else None;quote_skew=(max(quote_times)-min(quote_times)).total_seconds() if len(quote_times)>1 else 0.0;end_time=self._end_time(item);resolution_hours=max(.001,(end_time-observed).total_seconds()/3600) if end_time else 168
  quality=self.quality(item,quote_book);snapshot_payload={'schema':INPUT_SCHEMA_VERSION,'market':item,'yes_book':yes_book.model_dump() if yes_book else None,'no_book':no_book.model_dump() if no_book else None};snapshot_hash=hashlib.sha256(json.dumps(snapshot_payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  return MarketInput(market_id=str(item.get('id') or item.get('conditionId')),question=str(item.get('question','')),market_type=str(item.get('category') or item.get('eventType') or item.get('marketType') or 'unknown'),price=price,volume_24h=self._number(item.get('volume24hr') or item.get('volume24h')),liquidity=self._number(item.get('liquidity')),resolution_hours=resolution_hours,regime='baseline',source='polymarket-clob' if quote_book else 'polymarket-gamma',model_calibration_status='unavailable',observed_at=observed,quote_observed_at=quote_observed,quality_score=quality.score,snapshot_hash=snapshot_hash,yes_token_id=yes_token,no_token_id=no_token,yes_bid=yes_bid,yes_ask=yes_ask,yes_quote_observed_at=yes_observed,no_bid=no_bid,no_ask=no_ask,no_quote_observed_at=no_observed,quote_skew_seconds=quote_skew,book_bids=yes_book.bids if yes_book else [],book_asks=yes_book.asks if yes_book else [],yes_book_bids=yes_book.bids if yes_book else [],yes_book_asks=yes_book.asks if yes_book else [],no_book_bids=no_book.bids if no_book else [],no_book_asks=no_book.asks if no_book else [],book_sequence=quote_book.sequence if quote_book else None,market_status='active' if item.get('active',True) and not item.get('closed',False) else 'closed',market_end_time=end_time,fee_rate=float(os.getenv('PAPER_FEE_RATE','.02')),slippage_bps=float(os.getenv('PAPER_SLIPPAGE_BPS','10')))
 def quality(self,market,book=None):
  reasons=[];active=bool(market.get('active',True)) and not bool(market.get('closed',False));liquid=self._number(market.get('liquidity'))>=1000 and self._number(market.get('volume24hr') or market.get('volume24h'))>=5000
  if not active:reasons.append('market_not_active')
  if not liquid:reasons.append('insufficient_liquidity')
  executable=bool(book and book.best_ask is not None and book.best_bid is not None and book.best_bid<book.best_ask)
  if not executable:reasons.append('executable_book_unavailable')
  score=max(0,1-len(reasons)*.25)
  return MarketQuality(market_id=str(market.get('id') or market.get('conditionId') or ''),score=score,fresh=True,executable=executable,structurally_valid=True,liquid=liquid,active=active,reasons=reasons,observed_at=now_iso(),source='polymarket')
