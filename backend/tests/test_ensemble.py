from app.ensemble import adaptive_fast_weight, hybrid_forecast


class Memory:
 def __init__(self,decisions):self._decisions=decisions
 def decisions(self):return self._decisions


def test_hybrid_records_both_components_and_configured_weight(monkeypatch):
 monkeypatch.setenv('AUTO_PAPER_ENSEMBLE_FAST_WEIGHT','.6')
 result=hybrid_forecast({'model_version':'market_baseline_v2','probability':.5,'uncertainty':.2,'calibration_samples':0},{'model_version':'fast_market_v3','probability':.8,'uncertainty':.1,'calibration_samples':0},Memory([]))
 assert .5<result['probability']<.8
 assert result['model_version']=='hybrid_ensemble_v1'
 assert sum(item['weight'] for item in result['components'])==1
 assert result['weighting']['method']=='configured'


def test_hybrid_weight_adapts_only_after_resolved_research_samples(monkeypatch):
 from app.models import DecisionRecord,Mode
 monkeypatch.setenv('AUTO_PAPER_ENSEMBLE_MIN_SKILL_SAMPLES','3')
 base=dict(mode=Mode.PAPER,market_type='crypto',regime='range',action='BUY',side='YES',size=.01,price=.5,fair_probability=.5,confidence=.8,risk_score=1,edge=.1,rationale='test',research_eligible=True,outcome='win',resolved_yes=True)
 decisions=[]
 for index in range(3):
  decisions.append(DecisionRecord(id=f'fast-{index}',market_id=f'f-{index}',strategy_id='fast_model',model_probability=.8,**base))
  decisions.append(DecisionRecord(id=f'base-{index}',market_id=f'b-{index}',strategy_id='reference_class',model_probability=.5,**base))
 weight,details=adaptive_fast_weight(Memory(decisions))
 assert details['method']=='resolved_brier_adaptive'
 assert weight>.6
