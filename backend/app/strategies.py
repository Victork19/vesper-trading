import os
from dataclasses import dataclass,field
@dataclass
class Strategy:
    id:str; name:str; enabled:bool=True; market_types:list[str]=field(default_factory=list); min_edge:float=.03; max_size:float=.05; model_version:str|None=None; probability_path:str='unverified'; independent_model:bool=False; advisory_only:bool=False; promotion_status:str='experimental'; eligibility_universe:str='verified_execution_v1'; dependency_policy:str='canonical_event_family_v1'; benchmark_policy:str='fee_slippage_adjusted_executable_v1'; gate_metrics:list[str]=field(default_factory=lambda:['net_expectancy','return_on_capital','max_drawdown','calibration_drift'])
class StrategyRegistry:
    def __init__(self):
        # Only strategies with a real, tested probability path are enabled.
        # Named-but-unimplemented strategies must not create false research
        # buckets or imply predictive capability they do not yet possess.
        self.items={'reference_class':Strategy('reference_class','Reference Class',True,[],.03,.05,model_version='reference_class_v1',probability_path='reference_rate_or_market_baseline',independent_model=True,promotion_status='paper_only'),'paper_exploration':Strategy('paper_exploration','Paper Exploration',True,[],0.0,.01,model_version='paper_exploration_v1',probability_path='fixed_size_quote_sampling',independent_model=False,promotion_status='paper_only',advisory_only=True),'resolution_risk':Strategy('resolution_risk','Resolution Risk',False,[],.04,.03,model_version='unimplemented',probability_path='none',promotion_status='disabled'),'behavioral_bias':Strategy('behavioral_bias','Behavioral Bias',False,[],.05,.02,model_version='unimplemented',probability_path='none',promotion_status='disabled'),'relative_microstructure':Strategy('relative_microstructure','Relative Microstructure Ensemble',os.getenv('EXPERIMENTAL_STRATEGY_ENABLED','false').lower()=='true',[],.05,.02,model_version='experimental_microstructure_v1',probability_path='experimental_strategy.py',independent_model=False,promotion_status='experimental',advisory_only=True)}
    def get(self,id):return self.items.get(id)
    def all(self):return list(self.items.values())
