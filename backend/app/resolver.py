import os

from .engines import ScarEngine
from .market_data import PolymarketData
from .memory import TradingMemory
from .metrics import MetricsEngine
from .observability import telemetry
from .settlement import contract_pnl, parse_terminal_resolution, settle_decision


class OutcomeResolver:
    def __init__(self, memory=None, data=None):
        self.memory = memory or TradingMemory()
        self.data = data or PolymarketData()
        self.metrics = MetricsEngine(self.memory)
        self.scars = ScarEngine(self.memory)
        self.batch_size = max(1, int(os.getenv("RESOLUTION_BATCH_SIZE", "25")))
        self.enabled = os.getenv("RESOLUTION_ENABLED", "true").lower() == "true"

    def tick(self):
        if not self.enabled:
            telemetry.set("vesper_resolution_enabled", 0)
            return {"checked": 0, "settled": 0, "unresolved": 0, "errors": 0, "pending": 0, "disabled": True}
        telemetry.set("vesper_resolution_enabled", 1)
        checked = settled = unresolved = errors = 0
        pending = [decision for decision in self.memory.decisions() if decision.outcome == "pending" and decision.size > 0 and decision.paper_fill_fraction > 0 and not decision.market_id.startswith("manual-")]
        by_market = {}
        for decision in pending:
            by_market.setdefault(decision.market_id, []).append(decision)
        market_ids=sorted(by_market, key=lambda market_id:min(d.created_at for d in by_market[market_id]))
        market_cache = {}
        for market_id in market_ids[:self.batch_size]:
            checked += 1
            try:
                if market_id not in market_cache:
                    market_cache[market_id] = self.data.market(market_id)
                market = market_cache[market_id]
                resolved_yes = parse_terminal_resolution(market)
                if resolved_yes is None:
                    unresolved += len(by_market[market_id])
                    continue
                for decision in by_market[market_id]:
                    with self.memory.decision_lock(decision.id):
                        current = next((item for item in self.memory.decisions() if item.id == decision.id), None)
                        if current is None or current.outcome != "pending":
                            continue
                        result = contract_pnl(current, resolved_yes)
                        if result is None:
                            unresolved += 1
                            continue
                        outcome, pnl = result
                        settle_decision(self.memory,self.metrics,self.scars,current,outcome,pnl,clv=0.0,resolved_yes=resolved_yes,evidence_complete=True,source="polymarket_resolver",resolution={"market_id":market_id,"closed":market.get("closed"),"resolved":market.get("resolved"),"outcomes":market.get("outcomes"),"outcomePrices":market.get("outcomePrices")},process_score=1.0 if outcome=='win' else 0.0)
                        settled += 1
            except Exception as exc:
                errors += 1
                telemetry.error("outcome_resolution")
                continue
        telemetry.inc("vesper_resolution_ticks_total")
        telemetry.set("vesper_resolution_checked_markets", checked)
        telemetry.set("vesper_resolution_pending_markets", len(market_ids))
        telemetry.set("vesper_pending_decisions", len(pending))
        telemetry.set("vesper_last_resolved_count", settled)
        telemetry.set("vesper_last_resolution_errors", errors)
        return {"checked": checked, "checked_markets": checked, "settled": settled, "unresolved": unresolved, "errors": errors, "pending": len(pending), "pending_markets": len(market_ids)}
