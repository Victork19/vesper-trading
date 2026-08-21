from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
def now_iso(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
class Mode(str,Enum): PAPER='paper'; SHADOW='shadow'; LIVE='live'
class OrderStatus(str,Enum): NEW='new'; ACCEPTED='accepted'; PARTIALLY_FILLED='partially_filled'; FILLED='filled'; CANCELED='canceled'; REJECTED='rejected'; FAILED='failed'
class Impact(BaseModel): trust_delta:float=-.2; max_size_multiplier:float=.5; cooldown_hours:int=24; new_filters:list[str]=Field(default_factory=list); constitutional:bool=True
class Scar(BaseModel):
 id:str; strategy_id:str='unknown'; market_id:str='unknown'; market_type:str='unknown'; regime:str='unknown'; type:str; failure_type:str='negative_process'; severity:int=Field(ge=1,le=10); pnl:float=0; clv:float=0; process_score:float=Field(default=0,ge=0,le=1); lesson:str; principle:str; impact:Impact=Field(default_factory=Impact); affected_buckets:list[str]=Field(default_factory=list); cooldown_until:str|None=None; rehabilitation_condition:str='Require three qualifying positive resolved outcomes with non-negative CLV and no constitutional rule violations.'; rehabilitation_required:int=3; rehabilitation_progress:int=0; linked_scars:list[str]=Field(default_factory=list); status:str='active'; created_at:str=Field(default_factory=now_iso); resolved_at:str|None=None; onchain_anchor:str|None=None
class Principle(BaseModel): id:str; statement:str; source_scars:list[str]=Field(default_factory=list); strength:int=Field(default=1,ge=1,le=10); strategy_id:str='global'; regime:str='global'; status:str='active'; created_at:str=Field(default_factory=now_iso)
class ProcessSnapshot(BaseModel): strategy_id:str; market_type:str; regime:str; decisions:int=0; wins:int=0; pnl:float=0; clv_sum:float=0; expectancy:float=0; rule_adherence:float=1; decision_quality:float=.5; profit_factor:float=0; gross_profit:float=0; gross_loss:float=0; brier_score:float|None=None; log_loss:float|None=None; calibration_error:float|None=None; updated_at:str=Field(default_factory=now_iso)
class BookLevel(BaseModel): price:float=Field(ge=0,le=1); size:float=Field(ge=0)
class HotState(BaseModel): mode:Mode=Mode.PAPER; trust:dict[str,float]=Field(default_factory=dict); active_constraints:list[str]=Field(default_factory=list); open_risk:float=0; portfolio_heat:float=0; correlation_regime:str='baseline'; capacity_utilization:float=0; daily_pnl:float=0; weekly_pnl:float=0; last_context:str=''
class MarketInput(BaseModel):
 market_id:str; question:str; market_type:str='unknown'; price:float=Field(ge=0,le=1)
 volume_24h:float=Field(default=0,ge=0); liquidity:float=Field(default=0,ge=0); resolution_hours:float=Field(default=168,gt=0)
 regime:str='baseline'; reference_rate:float|None=Field(default=None,ge=0,le=1); signals:dict[str,float]=Field(default_factory=dict)
 source:str='manual'; observed_at:datetime|None=None; quote_observed_at:datetime|None=None; quality_score:float=Field(default=1,ge=0,le=1); snapshot_hash:str|None=None; market_status:str='active'; market_end_time:datetime|None=None; book_bids:list[BookLevel]=Field(default_factory=list); book_asks:list[BookLevel]=Field(default_factory=list); book_sequence:int|None=None
 yes_token_id:str|None=None; no_token_id:str|None=None
 yes_bid:float|None=Field(default=None,ge=0,le=1); yes_ask:float|None=Field(default=None,ge=0,le=1)
 no_bid:float|None=Field(default=None,ge=0,le=1); no_ask:float|None=Field(default=None,ge=0,le=1)
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
    resolved_yes:bool|None=None; order_id:str|None=None
    source:str='manual'; quality_score:float=1; snapshot_hash:str|None=None; observed_at:str|None=None; quote_observed_at:str|None=None; book_sequence:int|None=None

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
    venue_order_id:str|None=None; error:str|None=None; created_at:str=Field(default_factory=now_iso); updated_at:str=Field(default_factory=now_iso)
class OutcomeRequest(BaseModel):
 decision_id:str
 outcome:str
 close_price:float|None=None
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
