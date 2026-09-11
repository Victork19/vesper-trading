from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .engines import ScarEngine
from .metrics import MetricsEngine
from .models import DecisionRecord, EventQuality, now_iso
from .eda import make_canonical_event
from .observability import telemetry
from .attribution import evaluate_attribution
from .experiential_memory import build_postmortem


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
    process_score: float | None = None,
) -> DecisionRecord:
    """Apply one terminal outcome to a decision and update learning state."""
    if decision.outcome != "pending":
        return decision

    # Live settlement is derived from immutable fills, never from mutable
    # decision/order aggregates supplied by an API caller or old projection.
    # A Polymarket source is also used for paper/shadow observations.  Only a
    # live-mode decision has venue execution obligations; source provenance
    # must never turn a paper decision into an unresolved live settlement.
    live_execution = decision.mode.value == "live"
    fill_totals = None
    if live_execution:
        if not decision.order_id or resolved_yes is None or not decision.execution_reconciled:
            raise ValueError("live settlement requires a reconciled order and terminal resolution")
        with memory.db.connection() as c:
            order_row=c.execute("SELECT status,filled_size,filled_notional,filled_fees,average_fill_price FROM orders WHERE id=%s FOR SHARE",(decision.order_id,)).fetchone()
            if not order_row or order_row['status'] not in ('filled','canceled','expired','rejected','failed'):
                raise ValueError("live settlement requires a terminal order")
            fill_totals = c.execute("""
                SELECT COALESCE(SUM(quantity),0) AS quantity,
                       COALESCE(SUM(quantity*price),0) AS notional,
                       COALESCE(SUM(fee),0) AS fees
                FROM execution_fills WHERE order_id=%s
            """, (decision.order_id,)).fetchone()
        quantity=Decimal(str(fill_totals['quantity'] or 0))
        notional=Decimal(str(fill_totals['notional'] or 0))
        fees=Decimal(str(fill_totals['fees'] or 0))
        if quantity <= 0 or notional < 0 or fees < 0:
            raise ValueError("live settlement requires immutable fill records")
        average=notional/quantity
        if abs(float(order_row['filled_size'] or 0)-float(quantity))>1e-9 or abs(float(order_row['filled_notional'] or 0)-float(notional))>1e-8 or abs(float(order_row['filled_fees'] or 0)-float(fees))>1e-8:
            raise ValueError("live settlement requires order projection to match immutable fills")
        if order_row['average_fill_price'] is not None and abs(float(order_row['average_fill_price'])-float(average))>1e-8:
            raise ValueError("live settlement requires weighted average fill price")
        won=resolved_yes == (decision.side == "YES")
        realized=(quantity*(Decimal(1)-average) if won else -quantity*average)-fees
        pnl=float(realized)
        decision.executed_size=float(quantity)
        decision.executed_notional=float(notional)
        decision.executed_fees=float(fees)
        decision.executed_average_price=float(average)
        decision.execution_reconciled=True

    decision.outcome = outcome
    decision.pnl = pnl
    decision.clv = clv
    decision.resolved_yes = resolved_yes
    decision.resolved_at = now_iso()
    decision.market_context = {**(decision.market_context or {}), 'resolution_source': source, 'resolution_verified': True}
    episode = memory.episode_for_decision(decision.id)
    attribution = evaluate_attribution(decision, action_evaluations=episode.action_evaluations if episode else [])
    decision.attribution = attribution
    decision.evaluation_metrics = attribution["metrics"]
    decision.counterfactuals = {
        "best_available_alternative_utility": attribution["metrics"]["best_available_alternative_utility"],
        "selected_expected_utility": attribution["metrics"]["selected_expected_utility"],
        "status": "estimate_not_causal",
    }
    settlement_event = make_canonical_event("settlement", "outcome_resolved", {
        "decision_id": decision.id, "outcome": outcome, "pnl": pnl, "clv": clv,
        "resolved_yes": resolved_yes, "source": source, "resolution": resolution or {},
    }, event_time=decision.resolved_at, episode_id=decision.episode_id, source_event_id=decision.id,
       quality=EventQuality(confidence=1.0 if resolved_yes is not None else .5, valid=True))
    attribution_event = make_canonical_event("attribution", "outcome_attribution", {
        "decision_id": decision.id, "attribution": attribution,
    }, event_time=decision.resolved_at, episode_id=decision.episode_id, source_event_id=decision.id,
       quality=EventQuality(confidence=1.0, valid=True))

    with memory.decision_lock(decision.id):
      with memory.portfolio_lock() as portfolio_connection:
        hot = memory.hot_for_update(portfolio_connection)
        hot.daily_pnl += pnl
        hot.weekly_pnl += pnl
        if decision.execution_reconciled:
            effective_exposure=float(decision.executed_notional or 0)+float(decision.executed_fees or 0)
        else:
            paper_price=decision.paper_execution_price if decision.paper_execution_price is not None else decision.executable_price if decision.executable_price is not None else decision.price
            effective_exposure=decision.size*decision.paper_fill_fraction*paper_price+float((decision.market_context or {}).get('fee_rate',0) or 0)*decision.size*decision.paper_fill_fraction
        hot.portfolio_heat = max(0, hot.portfolio_heat - effective_exposure)
        hot.open_risk = max(0, hot.open_risk - effective_exposure)
        trust = hot.trust.get(decision.strategy_id, 0.5)
        hot.trust[decision.strategy_id] = max(0, min(1, trust + (.02 if pnl > 0 else -.05 if pnl < 0 else 0)))
        memory.save_settlement_state(decision,hot,portfolio_connection,eda_events=[settlement_event, attribution_event])
        reservation_count=memory.settle_reservations(decision.id,portfolio_connection)
        if live_execution and reservation_count == 0:
            raise ValueError("live settlement requires a capital reservation")
        # Settlement is also an append-only execution-ledger event. The
        # idempotency key makes retries harmless while preserving the
        # financial event history independently of mutable decision rows.
        entry_cost=float(fill_totals['notional']) if fill_totals is not None else float(max(0, decision.executed_notional or effective_exposure*(decision.executed_average_price or decision.paper_execution_price or decision.executable_price or decision.price)))
        gross_proceeds=float((fill_totals['quantity'] if fill_totals is not None else effective_exposure) if (resolved_yes == (decision.side == 'YES')) else 0)
        portfolio_connection.execute("""INSERT INTO execution_ledger
                (event_id,idempotency_key,decision_id,event_type,quantity,notional,fee,entry_cost,gross_proceeds,realized_pnl,cash_delta,payload,observed_at)
                VALUES(%s,%s,%s,'settlement',%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT(idempotency_key) DO NOTHING""",
                ('ledger_settle_'+decision.id, 'settlement:'+decision.id, decision.id,
                 float(decision.executed_size if decision.execution_reconciled else effective_exposure), entry_cost,
                 float(getattr(decision,'executed_fees',0) or 0), entry_cost, gross_proceeds, float(pnl), float(pnl),
                 memory.db.json({'outcome':outcome,'resolved_yes':resolved_yes,'source':source})))

    measured_process_score=max(0.0,min(1.0,process_score if process_score is not None else (1.0 if pnl>0 and evidence_complete else .5 if evidence_complete else .25)))
    snapshot = metrics.outcome(decision, pnl, clv, measured_process_score, resolved_yes, attribution=attribution)
    memory.event("outcome_recorded", {
        "decision_id": decision.id,
        "outcome": outcome,
        "settled_pnl": pnl,
        "source": source,
        "resolved_yes": resolved_yes,
        "resolution": resolution or {},
        "settled_at": now_iso(),
        "snapshot": snapshot.model_dump(),
        "attribution": attribution,
    })
    memory.put("failure" if outcome in ("loss", "failure", "negative") else "semantic", f"postmortem:{decision.id}", build_postmortem(decision, attribution))
    telemetry.inc("vesper_outcomes_total", labels={"outcome": outcome, "source": source})
    telemetry.set("vesper_daily_pnl", hot.daily_pnl)
    telemetry.set("vesper_weekly_pnl", hot.weekly_pnl)
    telemetry.set("vesper_portfolio_heat", hot.portfolio_heat)

    if outcome in ("loss", "failure", "negative"):
        failure_type='negative_outcome' if outcome=='loss' else 'negative_process'
        if decision.paper_cost>abs(decision.pnl)*.25 and decision.paper_cost>0: failure_type='cost_drag'
        elif decision.clv is not None and decision.clv<-.02: failure_type='negative_clv'
        elif decision.resolved_yes is not None and abs(decision.fair_probability-(1.0 if decision.resolved_yes else 0.0))>=.35: failure_type='model_miscalibration'
        scar, principle = scars.failure(decision, "Negative outcome or process result; require stronger evidence before repeating this bucket.",failure_type=failure_type,process_score=measured_process_score)
        decision.cited_scars.append(scar.id)
        decision.cited_principles.append(principle.id)
        memory.put("COLD", decision.id, decision.model_dump())
    elif outcome in ("win", "push"):
        scars.rehabilitate(decision,clv)
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
    explicit_winner = market.get("winner") or market.get("winningOutcome") or market.get("winning_outcome") or market.get("finalOutcome") or market.get("final_outcome") or market.get("result")
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
    if decision.mode.value == 'live' and not decision.execution_reconciled:
        return None
    executed_size=decision.executed_size if decision.execution_reconciled else decision.size*decision.paper_fill_fraction
    if executed_size <= 0 or decision.side not in ("YES", "NO"):
        return None
    won = resolved_yes == (decision.side == "YES")
    price = decision.executed_average_price if decision.executed_average_price is not None else decision.paper_execution_price if decision.paper_execution_price is not None else decision.executable_price if decision.executable_price is not None else decision.price
    if decision.execution_reconciled and decision.executed_average_price is None:
        return None
    size=executed_size
    fee=float(getattr(decision,'executed_fees',0) or 0) if decision.execution_reconciled else 0.0
    return ("win" if won else "loss", (size * (1 - price) if won else -size * price)-fee)
