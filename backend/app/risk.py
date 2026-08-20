from .models import MarketInput
class PortfolioRisk:
 def __init__(self,memory):self.memory=memory
 def heat(self):return self.memory.hot().portfolio_heat
 def gate(self,m,requested_size,flow_imbalance=0,large_wallet_signal=0):
  reasons=[];multiplier=1.0
  if self.memory.hot().daily_pnl<=-0.1:reasons.append('daily_kill_switch')
  if self.memory.hot().weekly_pnl<=-0.2:reasons.append('weekly_kill_switch')
  if abs(flow_imbalance)>.7 or large_wallet_signal>.8:reasons.append('toxic_flow');multiplier*=.25
  if m.resolution_hours>720:reasons.append('long_horizon_discount');multiplier*=.5
  return (0 if reasons and any(x in reasons for x in ['daily_kill_switch','weekly_kill_switch']) else requested_size*multiplier),reasons
