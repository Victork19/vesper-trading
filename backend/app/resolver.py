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
        pending = [decision for decision in self.memory.decisions() if decision.outcome == "pending" and decision.size > 0 and not decision.market_id.startswith("manual-")]
        for decision in pending[:self.batch_size]:
            checked += 1
            try:
                market = self.data.market(decision.market_id)
                resolved_yes = parse_terminal_resolution(market)
                if resolved_yes is None:
                    unresolved += 1
                    continue
                result = contract_pnl(decision, resolved_yes)
                if result is None:
                    unresolved += 1
                    continue
                outcome, pnl = result
                settle_decision(
                    self.memory,
                    self.metrics,
                    self.scars,
                    decision,
                    outcome,
                    pnl,
                    clv=0.0,
                    resolved_yes=resolved_yes,
                    evidence_complete=True,
                    source="polymarket_resolver",
                    resolution={
                        "market_id": decision.market_id,
                        "closed": market.get("closed"),
                        "resolved": market.get("resolved"),
                        "outcomes": market.get("outcomes"),
                        "outcomePrices": market.get("outcomePrices"),
                    },
                )
                settled += 1
            except Exception as exc:
                errors += 1
                telemetry.error("outcome_resolution")
                continue
        telemetry.inc("vesper_resolution_ticks_total")
        telemetry.set("vesper_pending_decisions", len(pending))
        telemetry.set("vesper_last_resolved_count", settled)
        telemetry.set("vesper_last_resolution_errors", errors)
        return {"checked": checked, "settled": settled, "unresolved": unresolved, "errors": errors, "pending": len(pending)}
