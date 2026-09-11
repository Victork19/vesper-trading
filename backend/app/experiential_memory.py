"""Evidence-linked experiential memory helpers."""

from __future__ import annotations

import re
from typing import Any, Iterable


MEMORY_TIERS = {"semantic", "procedural", "failure", "model"}


def build_postmortem(decision, attribution: dict[str, Any]) -> dict[str, Any]:
    categories = attribution.get("categories", {})
    dominant = max(
        ((float(value), key) for key, value in categories.items() if value is not None),
        default=(0.0, "none"),
    )[1]
    return {
        "type": "eda_postmortem_v1",
        "decision_id": decision.id,
        "episode_id": decision.episode_id,
        "market_id": decision.market_id,
        "strategy_id": decision.strategy_id,
        "outcome": decision.outcome,
        "pnl": decision.pnl,
        "dominant_category": dominant,
        "attribution": attribution,
        "evidence": {"episode_id": decision.episode_id, "resolved_at": decision.resolved_at},
    }


def rank_memories(memories: Iterable[dict[str, Any]], query: str, limit: int = 8, *, require_evidence: bool = True) -> list[dict[str, Any]]:
    terms = set(re.findall(r"[a-z0-9_]+", query.lower()))
    ranked = []
    for memory in memories:
        evidence = memory.get("evidence") or {}
        if require_evidence and not evidence.get("episode_id"):
            continue
        text = " ".join(str(value) for value in memory.values()).lower()
        score = sum(term in text for term in terms)
        if score:
            ranked.append((score, str(memory.get("created_at", "")), memory))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:max(1, limit)]]