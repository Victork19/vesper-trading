import os
from enum import StrEnum
class PipelineStage(StrEnum): BOOT='boot'; WARMUP='warmup'; PAPER='paper'; SHADOW='shadow'; LIVE_REQUESTED='live_requested'; LIVE='live'; HALT='halt'
class AutonomyGate:
 def __init__(self,memory,store):self.memory=memory;self.store=store
 def status(self):
  h=self.memory.hot();samples=self.store.count();minimum=int(os.getenv('MIN_MARKET_SNAPSHOTS','1000'));stage=h.mode.value
  return {'stage':stage,'snapshots':samples,'minimum_snapshots':minimum,'data_ready':samples>=minimum,'next_stage':'shadow' if samples>=minimum else 'paper','live_allowed':False,'reason':'Live mode requires explicit operator approval and separate capital gates.'}
