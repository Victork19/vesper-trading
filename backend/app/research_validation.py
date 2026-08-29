"""Quantitative validation primitives with explicit units and eligibility.

This module is deliberately independent of the API, database, and execution
layers.  It defines the population and measurement rules that later callers
can use consistently for calibration, research reports, and live gates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Iterable, Sequence


def _get(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _number(item: Any, name: str, default: float = 0.0) -> float:
    try:
        value = _get(item, name, default)
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return float(default)


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def canonical_dependency_metadata(
    question: str,
    market_type: str = "unknown",
    *,
    market_id: str | None = None,
    underlying_asset: str | None = None,
    event_id: str | None = None,
    resolution_start: Any = None,
    resolution_end: Any = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Return stable, non-guessing dependency metadata.

    Numeric values are retained: replacing every number with a wildcard can
    incorrectly merge different strikes or resolution windows.  A caller may
    provide an authoritative ``event_id``; otherwise the canonical question,
    asset, market type, and resolution bucket form a conservative family key.
    """
    canonical_question = re.sub(r"\s+", " ", str(question or "").strip().lower())
    start = _timestamp(resolution_start)
    end = _timestamp(resolution_end)
    if start and end:
        horizon = max(0, int((end - start).total_seconds() // 60))
        resolution_key = f"{start.isoformat()}::{end.isoformat()}"
    else:
        horizon = None
        resolution_key = "unknown"
    family_basis = {
        "event_id": _slug(event_id) if event_id else None,
        "question": canonical_question,
        "market_type": _slug(market_type) or "unknown",
        "underlying_asset": _slug(underlying_asset) or "unknown",
        "resolution": resolution_key,
    }
    encoded = json.dumps(family_basis, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "dependency_id": f"dep_{digest[:24]}",
        "event_family": f"family_{digest[:24]}",
        "canonical_question": canonical_question,
        "market_type": family_basis["market_type"],
        "underlying_asset": family_basis["underlying_asset"],
        "event_id": family_basis["event_id"],
        "resolution_key": resolution_key,
        "horizon_minutes": horizon,
        "source": source or "unknown",
        "provenance": "explicit_event_id" if event_id else "canonical_metadata_hash",
    }


def executable_benchmark(observation: Any, side: str | None = None) -> dict[str, float | str | None]:
    """Return the side-specific, fee/slippage-adjusted executable benchmark."""
    side = str(side or _get(observation, "side", "YES") or "YES").upper()
    context = _get(observation, "market_context", {}) or {}
    ask_name = "yes_ask" if side == "YES" else "no_ask"
    ask = _get(observation, ask_name, None)
    if ask is None:
        ask = context.get(ask_name)
    if ask is None:
        ask = _get(observation, "executable_price", None)
    if ask is None:
        ask = _get(observation, "price", None)
    price = max(0.0, min(1.0, _number({"value": ask}, "value")))
    fee_rate = _number(observation, "fee_rate", _number(context, "fee_rate", 0.0))
    slippage_bps = _number(observation, "slippage_bps", _number(context, "slippage_bps", 0.0))
    slippage = price * slippage_bps / 10000.0
    fee = fee_rate * max(0.0, 1.0 - price)
    return {"side": side, "price": price, "slippage": slippage, "fee": fee, "cost": price + slippage + fee, "source": ask_name if ask is not None else "fallback"}


def exposure_notional(quantity: float, price: float, fee_rate: float = 0.0, slippage_bps: float = 0.0) -> float:
    quantity = max(0.0, float(quantity))
    price = max(0.0, float(price))
    return quantity * (price + price * max(0.0, float(slippage_bps)) / 10000.0 + max(0.0, float(fee_rate)) * max(0.0, 1.0 - price))


def research_exposure(observation: Any) -> float:
    mode = str(_get(_get(observation, "mode", ""), "value", _get(observation, "mode", ""))).lower()
    if mode == "live":
        quantity = _number(observation, "executed_size")
        price = _number(observation, "executed_average_price", _number(observation, "price"))
        fees = _number(observation, "executed_fees")
        return max(0.0, quantity * price + fees)
    quantity = _number(observation, "size") * max(0.0, min(1.0, _number(observation, "paper_fill_fraction", 1.0)))
    benchmark = executable_benchmark(observation)
    return max(0.0, quantity * float(benchmark["cost"]))


def research_eligibility(observation: Any, *, as_of: Any = None) -> dict[str, Any]:
    """Evaluate one immutable research-universe membership decision."""
    reasons: list[str] = []
    if not bool(_get(observation, "research_eligible", False)):
        reasons.append("explicit_research_flag_required")
    if not str(_get(observation, "source", "")).lower().startswith("polymarket"):
        reasons.append("verified_polymarket_source_required")
    if not _get(observation, "snapshot_hash"):
        reasons.append("snapshot_hash_required")
    if not _get(observation, "quote_observed_at"):
        reasons.append("quote_timestamp_required")
    if not _get(observation, "model_version"):
        reasons.append("model_version_required")
    if _get(observation, "outcome") not in {"win", "loss", "push"}:
        reasons.append("terminal_outcome_required")
    if _get(observation, "resolved_yes") is None:
        reasons.append("authoritative_resolution_required")
    if not bool(_get(observation, "execution_reconciled", False)):
        reasons.append("execution_reconciliation_required")
    if research_exposure(observation) <= 0:
        reasons.append("positive_reconciled_exposure_required")
    context = _get(observation, "market_context", {}) or {}
    if not context.get("event_family") or not context.get("dependency_id"):
        reasons.append("canonical_dependency_metadata_required")
    if context.get("manual_settlement") or context.get("resolution_source") in {None, "manual"}:
        reasons.append("independent_resolution_provenance_required")
    created = _timestamp(_get(observation, "created_at"))
    resolved = _timestamp(_get(observation, "resolved_at"))
    cutoff = _timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
    if not created or not resolved or created >= resolved or resolved > cutoff:
        reasons.append("valid_temporal_order_required")
    return {"eligible": not reasons, "reasons": reasons, "exposure": research_exposure(observation), "event_family": context.get("event_family"), "dependency_id": context.get("dependency_id")}


def is_research_eligible(observation: Any, *, as_of: Any = None) -> bool:
    return bool(research_eligibility(observation, as_of=as_of)["eligible"])


def _cluster_key(item: Any) -> str:
    context = _get(item, "market_context", {}) or {}
    return str(context.get("dependency_id") or context.get("event_family") or _get(item, "market_id", "unknown"))


def walk_forward_splits(observations: Sequence[Any], *, train_size: int = 30, test_size: int = 10, embargo: int = 1) -> list[tuple[list[Any], list[Any]]]:
    ordered = sorted(observations, key=lambda x: _timestamp(_get(x, "created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    splits = []
    cursor = train_size
    while cursor + embargo < len(ordered):
        test_start = cursor + embargo
        test_end = min(len(ordered), test_start + test_size)
        if test_end <= test_start:
            break
        train = ordered[:cursor]
        test = ordered[test_start:test_end]
        train_clusters = {_cluster_key(x) for x in train}
        test = [x for x in test if _cluster_key(x) not in train_clusters]
        if test:
            splits.append((train, test))
        cursor += test_size
    return splits


def population_drift(reference: Sequence[float], current: Sequence[float], bins: int = 10, psi_threshold: float = .20) -> dict[str, float | bool]:
    if not reference or not current:
        return {"psi": 1.0, "drift": True, "threshold": psi_threshold}
    lower, upper = min(min(reference), min(current)), max(max(reference), max(current))
    if upper <= lower:
        return {"psi": 0.0, "drift": False, "threshold": psi_threshold}
    width = (upper - lower) / bins
    def counts(values):
        result = [0] * bins
        for value in values:
            index = min(bins - 1, max(0, int((value - lower) / width)))
            result[index] += 1
        total = len(values)
        return [(count + 1e-6) / (total + bins * 1e-6) for count in result]
    expected, actual = counts(reference), counts(current)
    psi = sum((a - e) * math.log(a / e) for e, a in zip(expected, actual))
    return {"psi": psi, "drift": psi >= psi_threshold, "threshold": psi_threshold}


def risk_metrics(pnls: Sequence[float], exposures: Sequence[float] | None = None) -> dict[str, float | None]:
    values = [float(x) for x in pnls]
    if not values:
        return {"count": 0, "win_rate": None, "expectancy": None, "return_on_capital": None, "profit_factor": None, "max_drawdown": None, "sharpe": None}
    capital = [max(0.0, float(x)) for x in (exposures or [1.0] * len(values))]
    returns = [p / e if e else 0.0 for p, e in zip(values, capital)]
    equity = 0.0; peak = 0.0; drawdown = 0.0
    for value in values:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    gains = sum(x for x in values if x > 0); losses = abs(sum(x for x in values if x < 0))
    avg = mean(values); avg_return = mean(returns); sd = math.sqrt(sum((x - avg_return) ** 2 for x in returns) / max(1, len(returns) - 1))
    return {"count": float(len(values)), "win_rate": sum(x > 0 for x in values) / len(values), "expectancy": avg, "return_on_capital": sum(values) / max(1e-12, sum(capital)), "profit_factor": gains / losses if losses else (float("inf") if gains else 0.0), "max_drawdown": drawdown, "sharpe": avg_return / sd * math.sqrt(len(returns)) if sd > 0 else None}


def benjamini_hochberg(p_values: Sequence[float], alpha: float = .05) -> dict[str, Any]:
    indexed = sorted((max(0.0, min(1.0, float(p))), i) for i, p in enumerate(p_values))
    cutoff = None
    for rank, (p_value, _) in enumerate(indexed, 1):
        if p_value <= alpha * rank / max(1, len(indexed)):
            cutoff = p_value
    return {"rejected": [i for p, i in indexed if cutoff is not None and p <= cutoff], "cutoff": cutoff, "alpha": alpha, "tests": len(indexed)}
