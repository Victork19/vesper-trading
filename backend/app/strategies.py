from dataclasses import dataclass,field
@dataclass
class Strategy:
    id:str; name:str; enabled:bool=True; market_types:list[str]=field(default_factory=list); min_edge:float=.03; max_size:float=.05
class StrategyRegistry:
    def __init__(self):
        self.items={'reference_class':Strategy('reference_class','Reference Class',True,[],.03,.05),'resolution_risk':Strategy('resolution_risk','Resolution Risk',True,[],.04,.03),'behavioral_bias':Strategy('behavioral_bias','Behavioral Bias',True,[],.05,.02)}
    def get(self,id):return self.items.get(id)
    def all(self):return list(self.items.values())
