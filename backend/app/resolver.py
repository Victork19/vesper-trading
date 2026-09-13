import logging,os
from datetime import datetime,timezone

from .engines import ScarEngine
from .market_data import PolymarketData
from .memory import TradingMemory
from .metrics import MetricsEngine
from .observability import telemetry
from .settlement import contract_pnl, parse_terminal_resolution, settle_decision

log=logging.getLogger('vesper.resolver')


class OutcomeResolver:
    def __init__(self, memory=None, data=None):
        self.memory = memory or TradingMemory()
        self.data = data or PolymarketData()
        self.metrics = MetricsEngine(self.memory)
        self.scars = ScarEngine(self.memory)
        self.batch_size = max(1, int(os.getenv("RESOLUTION_BATCH_SIZE", "25")))
        self.enabled = os.getenv("RESOLUTION_ENABLED", "true").lower() == "true"
        self.retry_base_seconds=max(5,int(os.getenv('RESOLUTION_RETRY_BASE_SECONDS','30')))
        self.retry_max_seconds=max(self.retry_base_seconds,int(os.getenv('RESOLUTION_RETRY_MAX_SECONDS','900')))

    def _retry_due(self,market_id):
        with self.memory.db.connection() as c:
            row=c.execute('SELECT next_retry_at FROM resolution_retries WHERE market_id=%s',(market_id,)).fetchone()
        return not row or row['next_retry_at']<=datetime.now(timezone.utc)

    def _record_retry(self,market_id,error):
        with self.memory.db.connection() as c:
            row=c.execute('SELECT attempts FROM resolution_retries WHERE market_id=%s FOR UPDATE',(market_id,)).fetchone()
            attempts=(int(row['attempts']) if row else 0)+1;delay=min(self.retry_max_seconds,self.retry_base_seconds*(2**min(10,attempts-1)))
            c.execute("INSERT INTO resolution_retries(market_id,attempts,next_retry_at,last_error,updated_at) VALUES(%s,%s,now()+(%s*interval '1 second'),%s,now()) ON CONFLICT(market_id) DO UPDATE SET attempts=EXCLUDED.attempts,next_retry_at=EXCLUDED.next_retry_at,last_error=EXCLUDED.last_error,updated_at=EXCLUDED.updated_at",(market_id,attempts,delay,str(error)))

    def _clear_retry(self,market_id):
        with self.memory.db.connection() as c:c.execute('DELETE FROM resolution_retries WHERE market_id=%s',(market_id,))

    def _past_resolution_time(self, decisions):
        now=datetime.now(timezone.utc)
        for decision in decisions:
            value=(decision.market_context or {}).get('market_end_time')
            if not value:
                continue
            try:
                end=datetime.fromisoformat(str(value).replace('Z','+00:00'))
                if end.tzinfo is None:
                    end=end.replace(tzinfo=timezone.utc)
                if end<=now:
                    return True
            except (TypeError,ValueError):
                continue
        return False

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
            # Once a paper market's expected end has passed, retry backoff
            # must not hide it indefinitely. Keep checking until Gamma
            # publishes an authoritative terminal result.
            if not self._retry_due(market_id) and not self._past_resolution_time(by_market[market_id]):
                continue
            checked += 1
            try:
                if market_id not in market_cache:
                    market_cache[market_id] = self.data.market(market_id)
                market = market_cache[market_id]
                resolved_yes = parse_terminal_resolution(market)
                if resolved_yes is None:
                    unresolved += len(by_market[market_id])
                    log.info('resolution pending market=%s closed=%s resolved=%s resolution=%s winner=%s outcomes=%s outcomePrices=%s endDate=%s',market_id,market.get('closed'),market.get('resolved'),market.get('resolution'),market.get('winner') or market.get('winningOutcome') or market.get('finalOutcome'),market.get('outcomes'),market.get('outcomePrices'),market.get('endDate') or market.get('end_date'))
                    self._record_retry(market_id,'market_not_terminal')
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
                self._clear_retry(market_id)
            except Exception as exc:
                errors += 1
                self._record_retry(market_id,exc)
                telemetry.error("outcome_resolution")
                continue
        telemetry.inc("vesper_resolution_ticks_total")
        telemetry.set("vesper_resolution_checked_markets", checked)
        telemetry.set("vesper_resolution_pending_markets", len(market_ids))
        telemetry.set("vesper_pending_decisions", len(pending))
        telemetry.set("vesper_last_resolved_count", settled)
        telemetry.set("vesper_last_resolution_errors", errors)
        log.info('resolution tick checked=%s settled=%s unresolved=%s errors=%s pending=%s',checked,settled,unresolved,errors,len(pending))
        return {"checked": checked, "checked_markets": checked, "settled": settled, "unresolved": unresolved, "errors": errors, "pending": len(pending), "pending_markets": len(market_ids)}
