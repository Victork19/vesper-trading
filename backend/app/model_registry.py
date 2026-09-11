"""Fail-closed model and policy promotion gates."""

from __future__ import annotations


def promotion_decision(record, *, sample_minimum: int = 20, operator: str | None = None) -> dict:
    metrics = record.metrics or {}
    sample_count = int(metrics.get("sample_count", 0) or 0)
    blockers = []
    if sample_count < sample_minimum:
        blockers.append("minimum_sample_size")
    if metrics.get("out_of_sample_passed") is not True:
        blockers.append("out_of_sample_gate")
    if metrics.get("risk_gate_passed") is not True:
        blockers.append("risk_gate")
    if metrics.get("execution_gate_passed") is not True:
        blockers.append("execution_gate")
    if not isinstance(metrics.get("oos_expectancy_ci_low"), (int, float)) or float(metrics["oos_expectancy_ci_low"]) <= 0:
        blockers.append("positive_out_of_sample_expectancy")
    if not isinstance(metrics.get("max_drawdown"), (int, float)) or float(metrics["max_drawdown"]) > .20:
        blockers.append("drawdown_limit")
    if not isinstance(metrics.get("execution_reconciliation_rate"), (int, float)) or float(metrics["execution_reconciliation_rate"]) < .99:
        blockers.append("reconciliation_reliability")
    if not isinstance(metrics.get("operational_error_rate"), (int, float)) or float(metrics["operational_error_rate"]) > .01:
        blockers.append("operational_error_rate")
    if not record.rollback_target:
        blockers.append("rollback_target")
    approved = not blockers and bool(operator)
    return {"approved": approved, "status": "active" if approved else "candidate", "blockers": blockers if not approved else [], "approved_by": operator if approved else None, "rollback_target": record.rollback_target}