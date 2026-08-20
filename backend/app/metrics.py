from .models import ProcessSnapshot,DecisionRecord,now_iso
class MetricsEngine:
 def __init__(self,memory):self.memory=memory
 def outcome(self,d, pnl, clv, quality=1.0):
  key=f'{d.strategy_id}:{d.market_id}:{d.regime}'
  existing=next((x for x in self.memory.snapshots() if x.strategy_id==d.strategy_id and x.market_type==d.market_type and x.regime==d.regime),None) or ProcessSnapshot(strategy_id=d.strategy_id,market_type=d.market_type,regime=d.regime)
  existing.decisions+=1;existing.wins+=1 if pnl>0 else 0;existing.pnl+=pnl;existing.clv_sum+=clv;existing.expectancy=existing.pnl/max(1,existing.decisions);existing.decision_quality=(existing.decision_quality*(existing.decisions-1)+quality)/existing.decisions;existing.rule_adherence=(existing.rule_adherence*(existing.decisions-1)+(1 if d.gates and 'all_risk_gates_passed' in d.gates else 0))/existing.decisions;existing.updated_at=now_iso();self.memory.put('WARM','metrics_'+key,existing.model_dump());self.memory.event('process_snapshot',existing.model_dump());return existing
