from __future__ import annotations

import json
from typing import Any

from .engines import ScarEngine
from .metrics import MetricsEngine
from .models import DecisionRecord, now_iso
from .observability import telemetry


def settle_decision(
    memory,
    metrics: MetricsEngine,
    scars: ScarEngine,
    decision: DecisionRecord,
    outcome: str,
    pnl: float,
    clv: float = 0.0,
    resolved_yes: bool | None = None,
    evidence_complete: bool = True,
    source: str = "operator",
    resolution: dict[str, Any] | None = None,
) -> DecisionRecord:
    """Apply one terminal outcome to a decision and update learning state."""
    if decision.outcome != "pending":
        return decision

    decision.outcome = outcome
    decision.pnl = pnl
    decision.clv = clv
    decision.resolved_yes = resolved_yes
    memory.put("COLD", decision.id, decision.model_dump())

    hot = memory.hot()
    hot.daily_pnl += pnl
    hot.weekly_pnl += pnl
    hot.portfolio_heat = max(0, hot.portfolio_heat - decision.size)
    trust = hot.trust.get(decision.strategy_id, 0.5)
    hot.trust[decision.strategy_id] = max(0, min(1, trust + (.02 if pnl > 0 else -.05 if pnl < 0 else 0)))
    memory.save_hot(hot)

    snapshot = metrics.outcome(decision, pnl, clv, 1.0 if evidence_complete else .5, resolved_yes)
    memory.event("outcome_recorded", {
        "decision_id": decision.id,
        "outcome": outcome,
        "settled_pnl": pnl,
        "source": source,
        "resolved_yes": resolved_yes,
        "resolution": resolution or {},
        "settled_at": now_iso(),
        "snapshot": snapshot.model_dump(),
    })
    telemetry.inc("vesper_outcomes_total", labels={"outcome": outcome, "source": source})
    telemetry.set("vesper_daily_pnl", hot.daily_pnl)
    telemetry.set("vesper_weekly_pnl", hot.weekly_pnl)
    telemetry.set("vesper_portfolio_heat", hot.portfolio_heat)

    if outcome in ("loss", "failure", "negative"):
        scar, principle = scars.failure(decision, "Negative outcome or process result; require stronger evidence before repeating this bucket.")
        decision.cited_scars.append(scar.id)
        decision.cited_principles.append(principle.id)
        memory.put("COLD", decision.id, decision.model_dump())
    return decision


def parse_terminal_resolution(market: dict[str, Any]) -> bool | None:
    """Return the resolved YES/NO result only when the market is unambiguously terminal."""
    resolved = market.get("resolved") is True or str(market.get("resolved", "")).lower() == "true" or market.get("resolution") not in (None, "", False)
    if not resolved and market.get("closed") is not True:
        return None

    outcomes = market.get("outcomes", [])
    prices = market.get("outcomePrices", [])
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = []
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = []
    explicit_winner = market.get("winner") or market.get("winningOutcome") or market.get("winning_outcome")
    if explicit_winner is not None:
        label = str(explicit_winner).strip().lower()
        if label in ("yes", "true", "1"): return True
        if label in ("no", "false", "0"): return False
    try:
        prices = [float(value) for value in prices]
    except (TypeError, ValueError):
        return None
    if len(prices) < 2 or sum(1 for value in prices if value >= .999) != 1 or sum(1 for value in prices if value <= .001) < 1:
        return None

    winner = next(index for index, value in enumerate(prices) if value >= .999)
    if isinstance(outcomes, list) and len(outcomes) > winner:
        label = str(outcomes[winner]).strip().lower()
        if label in ("yes", "true", "1"):
            return True
        if label in ("no", "false", "0"):
            return False
        return None
    # Polymarket binary markets conventionally order outcomes as Yes, No.
    return winner == 0


def contract_pnl(decision: DecisionRecord, resolved_yes: bool) -> tuple[str, float] | None:
    if decision.size <= 0 or decision.side not in ("YES", "NO"):
        return None
    won = resolved_yes == (decision.side == "YES")
    price = decision.executable_price if decision.executable_price is not None else decision.price
    return ("win" if won else "loss", decision.size * (1 - price) if won else -decision.size * price)
