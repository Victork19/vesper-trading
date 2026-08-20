import math
from dataclasses import dataclass
from .models import MarketInput, EdgeEstimate
@dataclass
class ReferenceClass:
    name:str; sample_size:int; positive_rate:float
class CalibrationEngine:
    def __init__(self): self.classes:dict[str,ReferenceClass]={}
    def add(self,name:str,outcomes:list[int]):
        if outcomes:self.classes[name]=ReferenceClass(name,len(outcomes),sum(outcomes)/len(outcomes))
    def prior(self,name:str,default=.5):return self.classes.get(name,ReferenceClass(name,0,default))
    def shrink(self,estimate:float,prior:float,sample_size:int):
        weight=min(.9,sample_size/(sample_size+100));return weight*estimate+(1-weight)*prior
    def calibrate(self,predicted:list[float],outcomes:list[int]):
        if not predicted or len(predicted)!=len(outcomes):return {'count':0,'brier':None,'log_loss':None}
        brier=sum((p-y)**2 for p,y in zip(predicted,outcomes))/len(predicted);ll=-sum(y*math.log(max(p,.0001))+(1-y)*math.log(max(1-p,.0001)) for p,y in zip(predicted,outcomes))/len(predicted);return {'count':len(predicted),'brier':brier,'log_loss':ll}
class ReferenceClassEngine:
    def __init__(self,calibration=None):self.calibration=calibration or CalibrationEngine()
    def calibrated_prior(self,name,default,outcomes):
        if not outcomes:return default
        empirical=sum(outcomes)/len(outcomes)
        return self.calibration.shrink(empirical,default,len(outcomes))
    def apply(self,m:MarketInput):
        prior=m.reference_rate if m.reference_rate is not None else self.calibration.prior(m.market_type).positive_rate
        return self.calibration.shrink(prior, self.calibration.prior(m.market_type).positive_rate, self.calibration.prior(m.market_type).sample_size)
