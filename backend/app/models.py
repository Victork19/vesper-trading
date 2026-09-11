from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any
def now_iso(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def pnl_bucket_keys(now=None):
 now=now or datetime.now(timezone.utc);iso=now.isocalendar();return now.date().isoformat(),f'{iso.year}-W{iso.week:02d}'
class Mode(str,Enum): PAPER='paper'; SHADOW='shadow'; LIVE='live'
class OrderStatus(str,Enum): NEW='new'; ACCEPTED='accepted'; PARTIALLY_FILLED='partially_filled'; FILLED='filled'; CANCELED='canceled'; REJECTED='rejected'; FAILED='failed'; EXPIRED='expired'; CANCEL_REQUESTED='cancel_requested'; UNKNOWN='unknown'; RECONCILIATION_REQUIRED='reconciliation_required'
class Impact(BaseModel): trust_delta:float=-.2; max_size_multiplier:float=.5; cooldown_hours:int=24; new_filters:list[str]=Field(default_factory=list); constitutional:bool=True
class Scar(BaseModel):
 id:str; strategy_id:str='unknown'; market_id:str='unknown'; market_type:str='unknown'; regime:str='unknown'; type:str; failure_type:str='negative_process'; severity:int=Field(ge=1,le=10); pnl:float=0; clv:float=0; process_score:float=Field(default=0,ge=0,le=1); lesson:str; principle:str; impact:Impact=Field(default_factory=Impact); affected_buckets:list[str]=Field(default_factory=list); context:dict[str,Any]=Field(default_factory=dict); counterfactual:str='Would this decision have remained positive after fees, slippage, and a conservative fill?' ; evidence_count:int=1; recovery_score:float=0; last_evaluated_at:str|None=None; cooldown_until:str|None=None; rehabilitation_condition:str='Require three qualifying positive resolved outcomes with non-negative CLV and no constitutional rule violations.'; rehabilitation_required:int=3; rehabilitation_progress:int=0; linked_scars:list[str]=Field(default_factory=list); status:str='active'; created_at:str=Field(default_factory=now_iso); resolved_at:str|None=None; onchain_anchor:str|None=None
class Principle(BaseModel): id:str; statement:str; source_scars:list[str]=Field(default_factory=list); strength:int=Field(default=1,ge=1,le=10); strategy_id:str='global'; regime:str='global'; status:str='active'; created_at:str=Field(default_factory=now_iso)
class ProcessSnapshot(BaseModel): strategy_id:str; market_type:str; regime:str; model_version:str|None=None; decisions:int=0; wins:int=0; pnl:float=0; clv_sum:float=0; expectancy:float=0; rule_adherence:float=1; decision_quality:float=.5; profit_factor:float=0; gross_profit:float=0; gross_loss:float=0; brier_score:float|None=None; log_loss:float|None=None; calibration_error:float|None=None; attribution_error:dict[str,float]=Field(default_factory=dict); counterfactual_regret_sum:float=0; execution_error_sum:float=0; updated_at:str=Field(default_factory=now_iso)
class BookLevel(BaseModel): price:float=Field(ge=0,le=1); size:float=Field(ge=0)
class HotState(BaseModel): mode:Mode=Mode.PAPER; trust:dict[str,float]=Field(default_factory=dict); active_constraints:list[str]=Field(default_factory=list); open_risk:float=0; portfolio_heat:float=0; correlation_regime:str='baseline'; capacity_utilization:float=0; daily_pnl:float=0; weekly_pnl:float=0; pnl_day:str=''; pnl_week:str=''; last_context:str=''
class ObjectivePolicy(BaseModel):
  version:str='objective_policy_v1'
  objective:str='risk_adjusted_expected_value'
  utility_weights:dict[str,float]=Field(default_factory=lambda:{'expected_return':1.0,'downside_risk':1.0,'transaction_cost':1.0,'drawdown_risk':1.0,'uncertainty':1.0,'operational_risk':1.0,'information_value':0.0})
  hard_constraints:list[str]=Field(default_factory=lambda:['portfolio_heat','market_exposure','liquidity','data_freshness','model_health','kill_switch','execution_reconciliation'])
  authority_boundary:str='risk_policy_has_final_authority'
  live_capital_default:bool=False

class EventQuality(BaseModel):
  completeness:float=Field(default=1.0,ge=0,le=1)
  latency_ms:int=Field(default=0,ge=0)
  confidence:float=Field(default=1.0,ge=0,le=1)
  missing_fields:list[str]=Field(default_factory=list)
  valid:bool=True

class CanonicalEvent(BaseModel):
  event_id:str
  episode_id:str|None=None
  source:str
  source_event_id:str|None=None
  event_type:str
  event_time:str
  ingestion_time:str=Field(default_factory=now_iso)
  payload:dict[str,Any]=Field(default_factory=dict)
  schema_version:str='eda_event_v1'
  quality:EventQuality=Field(default_factory=EventQuality)

class BeliefState(BaseModel):
  belief_state_id:str
  environment_id:str='prediction_market'
  market_id:str
  created_at:str=Field(default_factory=now_iso)
  observable_features:dict[str,Any]=Field(default_factory=dict)
  probability_distributions:dict[str,Any]=Field(default_factory=dict)
  uncertainty_intervals:dict[str,Any]=Field(default_factory=dict)
  regime_beliefs:dict[str,float]=Field(default_factory=dict)
  open_questions:list[str]=Field(default_factory=list)
  stale_fields:list[str]=Field(default_factory=list)
  provenance:dict[str,list[str]]=Field(default_factory=dict)
  source_event_ids:list[str]=Field(default_factory=list)
  schema_version:str='eda_belief_v1'

class DecisionEpisode(BaseModel):
  episode_id:str
  decision_id:str
  environment_id:str='prediction_market'
  timestamp_start:str
  timestamp_decision:str
  timestamp_end:str|None=None
  observation_snapshot:dict[str,Any]=Field(default_factory=dict)
  available_information:dict[str,Any]=Field(default_factory=dict)
  belief_state:BeliefState
  forecasts:dict[str,Any]=Field(default_factory=dict)
  uncertainty:dict[str,Any]=Field(default_factory=dict)
  regime:dict[str,Any]=Field(default_factory=dict)
  candidate_actions:list[dict[str,Any]]=Field(default_factory=list)
  consequence_predictions:dict[str,Any]=Field(default_factory=dict)
  selected_action:dict[str,Any]=Field(default_factory=dict)
  risk_decision:dict[str,Any]=Field(default_factory=dict)
  execution_result:dict[str,Any]=Field(default_factory=dict)
  realized_outcome:dict[str,Any]|None=None
  attribution:dict[str,Any]=Field(default_factory=dict)
  counterfactuals:dict[str,Any]=Field(default_factory=dict)
  evaluation_metrics:dict[str,Any]=Field(default_factory=dict)
  memory_links:dict[str,Any]=Field(default_factory=dict)
  model_versions:dict[str,str|None]=Field(default_factory=dict)
  attention_plan:dict[str,Any]=Field(default_factory=dict)
  information_requests:list[dict[str,Any]]=Field(default_factory=list)
  action_evaluations:list[dict[str,Any]]=Field(default_factory=list)
  policy_proposal:dict[str,Any]=Field(default_factory=dict)
  policy_version:str='deterministic_policy_v1'
  risk_policy_version:str='risk_policy_v1'
  data_versions:dict[str,str]=Field(default_factory=dict)
  objective_policy_version:str='objective_policy_v1'
  schema_version:str='eda_episode_v1'

class InformationRequest(BaseModel):
  request_id:str
  request_type:str
  target:str
  reason:str
  expected_value:float=0.0
  cost:float=0.0
  required:bool=False
  status:str='planned'
  policy_version:str='information_policy_v1'

class AttentionPlan(BaseModel):
  plan_id:str
  market_id:str
  strategy_id:str='reference_class'
  attention_score:float=Field(default=0.0,ge=0,le=1)
  expected_decision_value:float=Field(default=0.0,ge=0,le=1)
  uncertainty:float=Field(default=0.0,ge=0,le=1)
  time_sensitivity:float=Field(default=0.0,ge=0,le=1)
  actionability:float=Field(default=0.0,ge=0,le=1)
  analysis_cost:float=Field(default=0.0,ge=0,le=1)
  disposition:str='analyze'
  reasons:list[str]=Field(default_factory=list)
  information_requests:list[InformationRequest]=Field(default_factory=list)
  policy_version:str='attention_policy_v1'

class ActionEvaluation(BaseModel):
  action:str
  side:str|None=None
  available:bool=True
  executable_price:float|None=None
  probability:float|None=None
  expected_value_per_unit:float=0.0
  expected_utility:float=0.0
  downside_risk:float=0.0
  transaction_cost:float=0.0
  fill_probability:float=0.0
  liquidity_depth:float=0.0
  time_to_resolution_hours:float=0.0
  probability_of_loss:float=Field(default=0.0,ge=0,le=1)
  tail_risk:float=Field(default=0.0,ge=0)
  liquidity_impact:float=Field(default=0.0,ge=0)
  portfolio_effect:float=0.0
  opportunity_cost:float=0.0
  reversibility:float=Field(default=1.0,ge=0,le=1)
  paper_fill_price:float|None=None
  reasons:list[str]=Field(default_factory=list)
  model_dependent:bool=True
  policy_version:str='consequence_policy_v1'

class PolicyProposal(BaseModel):
  policy_version:str='deterministic_policy_v2'
  proposed_action:str='DO NOTHING'
  proposed_side:str|None=None
  expected_utility:float=0.0
  alternatives:list[dict[str,Any]]=Field(default_factory=list)
  rationale:str='No executable action was supported by the consequence evaluation.'
  risk_authority_required:bool=True
  status:str='proposed'
class MarketInput(BaseModel):
 market_id:str; question:str; market_type:str='unknown'; price:float=Field(ge=0,le=1)
 volume_24h:float=Field(default=0,ge=0); volume_known:bool=True; liquidity:float=Field(default=0,ge=0); resolution_hours:float=Field(default=168,gt=0)
 regime:str='baseline'; reference_rate:float|None=Field(default=None,ge=0,le=1); signals:dict[str,float]=Field(default_factory=dict); model_probability:float|None=Field(default=None,ge=0,le=1); model_lower_bound:float|None=Field(default=None,ge=0,le=1); model_upper_bound:float|None=Field(default=None,ge=0,le=1); model_uncertainty:float|None=Field(default=None,ge=0,le=1); model_calibration_samples:int=Field(default=0,ge=0); model_calibration_status:str='unavailable'
 source:str='manual'; model_version:str|None=None; model_provenance:dict[str,Any]=Field(default_factory=dict); raw_model_probability:float|None=None; observed_at:datetime|None=None; quote_observed_at:datetime|None=None; quality_score:float=Field(default=1,ge=0,le=1); snapshot_hash:str|None=None; market_status:str='active'; market_end_time:datetime|None=None; book_bids:list[BookLevel]=Field(default_factory=list); book_asks:list[BookLevel]=Field(default_factory=list); book_sequence:int|None=None
 yes_token_id:str|None=None; no_token_id:str|None=None; yes_book_bids:list[BookLevel]=Field(default_factory=list); yes_book_asks:list[BookLevel]=Field(default_factory=list); no_book_bids:list[BookLevel]=Field(default_factory=list); no_book_asks:list[BookLevel]=Field(default_factory=list)
 yes_bid:float|None=Field(default=None,ge=0,le=1); yes_ask:float|None=Field(default=None,ge=0,le=1); yes_quote_observed_at:datetime|None=None
 no_bid:float|None=Field(default=None,ge=0,le=1); no_ask:float|None=Field(default=None,ge=0,le=1); no_quote_observed_at:datetime|None=None; quote_skew_seconds:float=Field(default=0,ge=0)
 fee_rate:float=Field(default=0,ge=0,le=.5); slippage_bps:float=Field(default=0,ge=0,le=10000)

 @field_validator('signals')
 @classmethod
 def valid_signals(cls,value):
  if any(not isinstance(v,(int,float)) or v<0 or v>1 for v in value.values()):raise ValueError('signals must be probabilities between 0 and 1')
  return value

 @model_validator(mode='after')
 def valid_quotes(self):
  if self.yes_bid is not None and self.yes_ask is not None and self.yes_bid>self.yes_ask:raise ValueError('yes_bid cannot exceed yes_ask')
  if self.no_bid is not None and self.no_ask is not None and self.no_bid>self.no_ask:raise ValueError('no_bid cannot exceed no_ask')
  if self.model_lower_bound is not None and self.model_upper_bound is not None and self.model_lower_bound>self.model_upper_bound:raise ValueError('model_lower_bound cannot exceed model_upper_bound')
  if self.model_probability is not None and ((self.model_lower_bound is not None and self.model_probability<self.model_lower_bound) or (self.model_upper_bound is not None and self.model_probability>self.model_upper_bound)):raise ValueError('model_probability must lie within model bounds')
  return self
class EdgeEstimate(BaseModel):
    market_id:str
    fair_probability:float
    confidence:float
    uncertainty:float
    edge_sources:list[str]
    raw_edge:float
    recommended_side:str
    side_probability:float
    executable_price:float
    yes_edge:float
    no_edge:float
    aggregation_method:str='logit_shrinkage'
class DecisionRequest(BaseModel):
 market:MarketInput
 strategy_id:str='reference_class'
 execute:bool=False
 flow_imbalance:float=Field(default=0,ge=-1,le=1)
 large_wallet_signal:float=Field(default=0,ge=0,le=1)
 evidence_complete:bool=True
class DecisionRecord(BaseModel):
    id:str; created_at:str=Field(default_factory=now_iso); mode:Mode; market_id:str; strategy_id:str
    market_type:str='unknown'; regime:str='baseline'; action:str; side:str|None=None; size:float=0
    price:float; fair_probability:float; confidence:float; risk_score:int; edge:float
    executable_price:float|None=None; expected_value:float=0; rationale:str; cited_scars:list[str]=Field(default_factory=list)
    cited_principles:list[str]=Field(default_factory=list); gates:list[str]=Field(default_factory=list)
    status:str='paper'; outcome:str='pending'; pnl:float=0; clv:float|None=None
    resolved_yes:bool|None=None; resolved_at:str|None=None; order_id:str|None=None
    source:str='manual'; model_version:str|None=None; raw_model_probability:float|None=None; model_probability:float|None=Field(default=None,ge=0,le=1); quality_score:float=1; snapshot_hash:str|None=None; observed_at:str|None=None; quote_observed_at:str|None=None; book_sequence:int|None=None
    fill_model_version:str|None=None; model_provenance:dict[str,Any]=Field(default_factory=dict); model_lower_bound:float|None=Field(default=None,ge=0,le=1); model_upper_bound:float|None=Field(default=None,ge=0,le=1); model_uncertainty:float|None=Field(default=None,ge=0,le=1); model_calibration_samples:int=Field(default=0,ge=0); model_calibration_status:str='unavailable'; paper_fill_fraction:float=Field(default=1,ge=0,le=1); paper_execution_price:float|None=Field(default=None,ge=0,le=1); paper_cost:float=Field(default=0,ge=0); paper_fill_reason:str|None=None; executed_size:float=Field(default=0,ge=0); executed_notional:float=Field(default=0,ge=0); executed_fees:float=Field(default=0,ge=0); executed_average_price:float|None=Field(default=None,ge=0,le=1); execution_reconciled:bool=False; research_eligible:bool=False; market_context:dict[str,Any]=Field(default_factory=dict)

    episode_id:str|None=None;belief_state_id:str|None=None;objective_policy_version:str='objective_policy_v1';policy_version:str='deterministic_policy_v1';risk_policy_version:str='risk_policy_v1';data_version:str='market_input_v1'

    @model_validator(mode='after')
    def valid_model_bounds(self):
        if self.model_lower_bound is not None and self.model_upper_bound is not None and self.model_lower_bound>self.model_upper_bound:raise ValueError('model_lower_bound cannot exceed model_upper_bound')
        if self.model_probability is not None and ((self.model_lower_bound is not None and self.model_probability<self.model_lower_bound) or (self.model_upper_bound is not None and self.model_probability>self.model_upper_bound)):raise ValueError('model_probability must lie within model bounds')
        return self

class OrderBook(BaseModel):
    token_id:str; observed_at:str; bids:list[BookLevel]=Field(default_factory=list); asks:list[BookLevel]=Field(default_factory=list)
    best_bid:float|None=None; best_ask:float|None=None; bid_depth:float=0; ask_depth:float=0; source:str='polymarket-clob'; sequence:int|None=None

    @model_validator(mode='after')
    def validate_book(self):
        if any(self.bids[i].price<self.bids[i+1].price for i in range(len(self.bids)-1)):raise ValueError('bids must be descending')
        if any(self.asks[i].price>self.asks[i+1].price for i in range(len(self.asks)-1)):raise ValueError('asks must be ascending')
        if self.best_bid is not None and self.best_ask is not None and self.best_bid>=self.best_ask:raise ValueError('crossed order book')
        if self.best_bid is not None and self.bids and self.best_bid!=self.bids[0].price:raise ValueError('best_bid inconsistent with bids')
        if self.best_ask is not None and self.asks and self.best_ask!=self.asks[0].price:raise ValueError('best_ask inconsistent with asks')
        return self

class MarketQuality(BaseModel):
    market_id:str; score:float=Field(ge=0,le=1); fresh:bool; executable:bool; structurally_valid:bool; liquid:bool; active:bool
    reasons:list[str]=Field(default_factory=list); observed_at:str|None=None; source:str='unknown'

class OrderRecord(BaseModel):
    id:str; client_order_id:str; decision_id:str; mode:Mode; market_id:str; side:str; requested_size:float
    limit_price:float; status:OrderStatus=OrderStatus.NEW; filled_size:float=0; average_fill_price:float|None=None
    venue_order_id:str|None=None; error:str|None=None; created_at:str=Field(default_factory=now_iso); updated_at:str=Field(default_factory=now_iso); filled_notional:float=Field(default=0,ge=0); filled_fees:float=Field(default=0,ge=0)
class OutcomeRequest(BaseModel):
 decision_id:str
 outcome:str
 close_price:float|None=Field(default=None,ge=0,le=1)
 pnl:float=0
 clv:float=0
 evidence_complete:bool=True
 process_score:float|None=Field(default=None,ge=0,le=1)
 resolved_yes:bool|None=None

 @field_validator('outcome')
 @classmethod
 def valid_outcome(cls,value):
  if value not in {'win','loss','failure','negative','void','push'}:
   raise ValueError('outcome must be win, loss, failure, negative, void, or push')
  return value

class OpportunityObservation(BaseModel):
    opportunity_id:str; market_id:str; strategy_id:str='reference_class'; observed_at:str=Field(default_factory=now_iso); status:str='discovered'; reason:str|None=None; snapshot_hash:str|None=None; episode_id:str|None=None; payload:dict[str,Any]=Field(default_factory=dict); schema_version:str='eda_opportunity_v1'

class ModelRegistryRecord(BaseModel):
    model_id:str; version:str; kind:str='model'; status:str='candidate'; training_start:str|None=None; training_end:str|None=None; feature_version:str|None=None; dependencies:list[str]=Field(default_factory=list); metrics:dict[str,Any]=Field(default_factory=dict); limitations:list[str]=Field(default_factory=list); rollback_target:str|None=None; approved_by:str|None=None; created_at:str=Field(default_factory=now_iso)

class ReplayRun(BaseModel):
    replay_id:str; episode_id:str; mode:str='historical'; as_of:str; result:dict[str,Any]=Field(default_factory=dict); created_at:str=Field(default_factory=now_iso)
