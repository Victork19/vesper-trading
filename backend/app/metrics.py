import math
from .models import ProcessSnapshot,DecisionRecord,now_iso
class MetricsEngine:
 def __init__(self,memory):self.memory=memory
 def outcome(self,d, pnl, clv, quality=1.0, resolved_yes=None):
  key=f'{d.strategy_id}:{d.market_id}:{d.regime}'
  existing=next((x for x in self.memory.snapshots() if x.strategy_id==d.strategy_id and x.market_type==d.market_type and x.regime==d.regime),None) or ProcessSnapshot(strategy_id=d.strategy_id,market_type=d.market_type,regime=d.regime)
  prior=existing.decisions;existing.decisions+=1;existing.wins+=1 if pnl>0 else 0;existing.pnl+=pnl;existing.clv_sum+=clv;existing.gross_profit+=max(0,pnl);existing.gross_loss+=abs(min(0,pnl));existing.profit_factor=existing.gross_profit/existing.gross_loss if existing.gross_loss else existing.gross_profit;existing.expectancy=existing.pnl/max(1,existing.decisions);existing.decision_quality=(existing.decision_quality*prior+quality)/existing.decisions;existing.rule_adherence=(existing.rule_adherence*prior+(1 if d.gates and 'all_risk_gates_passed' in d.gates else 0))/existing.decisions
  if resolved_yes is not None:
   predicted=max(.000001,min(.999999,d.fair_probability));actual=1.0 if resolved_yes else 0.0;existing.brier_score=((existing.brier_score or 0)*prior+(predicted-actual)**2)/existing.decisions;existing.log_loss=((existing.log_loss or 0)*prior-(actual*math.log(predicted)+(1-actual)*math.log(1-predicted)))/existing.decisions;existing.calibration_error=((existing.calibration_error or 0)*prior+abs(predicted-actual))/existing.decisions
  existing.updated_at=now_iso();self.memory.put('WARM','metrics_'+key,existing.model_dump());self.memory.event('process_snapshot',existing.model_dump());return existing
