import math,uuid
from datetime import datetime,timedelta,timezone
from .models import *
class EdgeEngine:
 def estimate(self,m,calibrated_prior=None):
  prior=calibrated_prior if calibrated_prior is not None else (m.reference_rate if m.reference_rate is not None else .5)
  values=[max(.001,min(.999,prior))]+[max(.001,min(.999,x)) for x in m.signals.values()]
  logits=[math.log(x/(1-x)) for x in values]
  avg=sum(logits)/len(logits);fair=1/(1+math.exp(-avg))
  unc=min(.5,(max(values)-min(values)) if len(values)>1 else .2)
  conf=max(.05,min(.95,1-unc))
  # A last-trade price is not executable. Use the ask when supplied and apply
  # conservative slippage/fees to every paper estimate.
  slip=m.slippage_bps/10000
  yes_price=m.yes_ask if m.yes_ask is not None else min(1,m.price+slip)
  no_mid=1-m.price
  no_price=m.no_ask if m.no_ask is not None else min(1,no_mid+slip)
  yes_cost=yes_price+m.fee_rate*(1-yes_price)
  no_cost=no_price+m.fee_rate*(1-no_price)
  yes_edge=fair-yes_cost;no_edge=(1-fair)-no_cost
  side='YES' if yes_edge>=no_edge else 'NO'
  side_probability=fair if side=='YES' else 1-fair
  executable_price=yes_cost if side=='YES' else no_cost
  return EdgeEstimate(market_id=m.market_id,fair_probability=fair,confidence=conf,uncertainty=unc,edge_sources=['reference_class']+list(m.signals),raw_edge=max(yes_edge,no_edge),recommended_side=side,side_probability=side_probability,executable_price=executable_price,yes_edge=yes_edge,no_edge=no_edge)
class RiskEngine:
 def __init__(self,memory):self.memory=memory
 def size(self,e,m,trust,strategy,max_heat):
  if m.liquidity<1000 or m.volume_24h<5000:return 0,['liquidity_gate']
  if e.raw_edge<strategy.min_edge:return 0,['minimum_edge_gate']
  if trust<.25:return 0,['trust_gate']
  if self.memory.hot().portfolio_heat>=max_heat:return 0,['portfolio_heat_gate']
  capacity=min(1,m.liquidity/100000)
  corr=.5 if self.memory.hot().correlation_regime=='elevated' else .7 if self.memory.hot().correlation_regime=='crisis' else .2
  # Half-Kelly on the executable binary contract, capped by strategy policy.
  denominator=max(.01,1-e.executable_price)
  kelly=max(0,(e.side_probability-e.executable_price)/denominator)
  size=min(strategy.max_size,kelly*.5)*trust*capacity*(1-corr/2)
  if self.memory.hot().portfolio_heat+size>max_heat:
   size=max(0,max_heat-self.memory.hot().portfolio_heat)
   return size,['portfolio_heat_cap','all_risk_gates_passed'] if size else ['portfolio_heat_gate']
  return max(0,size),['all_risk_gates_passed']
class ScarEngine:
 def __init__(self,memory):self.memory=memory
 def failure(self,d,reason='negative process outcome',failure_type=None,process_score=0.0):
  failure_type=failure_type or ('negative_clv' if d.clv is not None and d.clv<0 else 'large_loss')
  severity=min(10,max(1,6+int(abs(min(0,d.pnl))*2)+int(abs(min(0,d.clv or 0))*10)))
  principle='Reduce size and require stronger evidence before repeating this strategy and market condition.'
  now=datetime.now(timezone.utc);cooldown_hours=24+severity*4
  s=Scar(id='scar_'+uuid.uuid4().hex[:8],strategy_id=d.strategy_id,market_id=d.market_id,market_type=d.market_type,regime=d.regime,type=failure_type,failure_type=failure_type,severity=severity,pnl=d.pnl,clv=d.clv or 0,process_score=process_score,lesson=reason,principle=principle,impact=Impact(trust_delta=-min(.4,.1+severity*.02),max_size_multiplier=max(.2,.8-severity*.05),cooldown_hours=cooldown_hours),affected_buckets=[d.strategy_id,d.market_type,d.regime],cooldown_until=(now+timedelta(hours=cooldown_hours)).isoformat().replace('+00:00','Z'))
  self.memory.put('WARM',s.id,s.model_dump());self.memory.event('scar_created',s.model_dump());p=Principle(id='principle_'+uuid.uuid4().hex[:7],statement=s.principle,source_scars=[s.id],strength=min(10,3+severity//2),strategy_id=d.strategy_id,regime=d.regime);self.memory.put('WARM',p.id,p.model_dump());return s,p
 def rehabilitate(self,d,clv=0.0):
  changed=[]
  for scar in self.memory.scars():
   if scar.status in ('resolved','rehabilitated') or scar.strategy_id!=d.strategy_id or scar.market_type not in (d.market_type,'unknown','global') or scar.regime not in (d.regime,'unknown','global'):continue
   if clv<0 or any(x in d.gates for x in ('daily_kill_switch','weekly_kill_switch','scar_constitutional_stop')):continue
   scar.status='rehabilitating';scar.rehabilitation_progress=min(scar.rehabilitation_required,scar.rehabilitation_progress+1);scar.impact.max_size_multiplier=min(1.0,scar.impact.max_size_multiplier+.1)
   if scar.rehabilitation_progress>=scar.rehabilitation_required:scar.status='rehabilitated';scar.resolved_at=now_iso();scar.impact.trust_delta=0;scar.impact.max_size_multiplier=1.0
   self.memory.put('WARM',scar.id,scar.model_dump());self.memory.event('scar_rehabilitation_progress',{'scar_id':scar.id,'progress':scar.rehabilitation_progress,'required':scar.rehabilitation_required,'status':scar.status});changed.append(scar)
  return changed
