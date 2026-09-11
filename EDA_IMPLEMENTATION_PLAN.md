# EDA Implementation Plan

## Objective

Implement Experiential Decision Architecture around Vesper's existing paper-first trading core without weakening the existing risk authority boundary.

The governing rule is:

> Forecasting and learning may recommend an action; the risk layer retains final authority.

EDA should make every decision measurable, reconstructible, replayable, and attributable before introducing more autonomy.

## Current status

Core implementation for phases 0–14 is present, including policy-driven
utility, evidence-linked memory, point-in-time replay boundaries, promotion
gates, opportunity tracking, and operator inspection paths. Production-grade
confidence still requires the configured PostgreSQL integration and release
test runtime.

Implemented foundation:

- Versioned risk-adjusted objective policy.
- Immutable decision episodes.
- Append-only canonical event storage.
- Market-ingestion, decision, execution, and settlement events.
- Belief states with market, model, regime, portfolio, freshness, and provenance data.
- Deterministic attention plans and active information requests.
- Policy-driven consequence evaluations for `DO NOTHING`, `WAIT`, `BUY YES`, and `BUY NO`.
- Versioned policy proposals that require positive executable utility before exposure.
- Episode and event inspection endpoints.
- Database triggers preventing episode/event mutation or deletion.

Primary implementation files:

- `backend/app/eda.py`
- `backend/app/models.py`
- `backend/app/memory.py`
- `backend/migrations/0010_eda_foundation.sql`
- `backend/tests/test_eda_foundation.py`

## Completed phases

### Phase 0 — Objectives and authority

Define the formal objective and non-negotiable authority boundary.

Implemented:

- `objective_policy_v1`.
- Risk-adjusted expected value as the objective.
- Utility weights for return, downside risk, transaction cost, drawdown, uncertainty, operational risk, and information value.
- Explicit hard constraints for heat, exposure, liquidity, freshness, model health, kill switches, and reconciliation.
- Persistent objective policy in the `REFERENCE` memory tier.

Acceptance criteria:

- Learned or forecast components cannot bypass hard risk gates.
- Every decision stores objective, policy, risk-policy, and data versions.

### Phase 1 — Immutable decision episodes

Create the episode as the unit of experience.

Implemented:

- `DecisionEpisode` schema.
- Persistent `eda_episodes` table.
- Immutable episode hash.
- Episode linkage from `DecisionRecord`.
- Candidate action set containing `DO NOTHING`, `BUY YES`, and `BUY NO`.
- Stored observations, forecasts, uncertainty, selected action, risk decision, and model versions.

Acceptance criteria:

- Every new decision receives an episode ID.
- The original decision context is preserved independently from later mutable projections.

### Phase 2 — Canonical event normalization

Represent external and internal observations with a common event envelope.

Implemented:

- `CanonicalEvent` and `EventQuality` schemas.
- Persistent `eda_events` table.
- Source and ingestion timestamps.
- Source IDs, event types, schema versions, payloads, and quality metadata.
- Events for market snapshots, decision evaluation, execution results, failures, reconciliation requirements, and outcomes.
- Append-only database triggers.

Acceptance criteria:

- Historical events are not overwritten or deleted.
- Event payloads retain the evidence used by the decision process.

### Phase 3 — Belief-state layer

Represent what the system believed to be true at decision time.

Implemented:

- `BeliefState` schema.
- Observable market and portfolio features.
- Probability distributions and uncertainty intervals.
- Regime beliefs.
- Stale fields and open questions.
- Provenance links to canonical source events.

Acceptance criteria:

- Every decision episode contains a belief state.
- Material beliefs can be traced to a source event or snapshot.

## Phase details

### Phase 4 — Attention and active information acquisition — implemented

Build a deterministic attention layer that decides which markets deserve analysis, additional data, delay, or operator review.

Implement:

- `AttentionPlan` model.
- Candidate-market priority scoring.
- Priority based on expected decision value, uncertainty, time sensitivity, actionability, and analysis cost.
- Explicit reasons for analyzing, skipping, delaying, or escalating a market.
- Information requests for fresh metadata, fresh books, resolution checks, historical prices, confirmation events, and operator approval.
- Initial rule-based information policy.
- Telemetry for attention decisions and information-request outcomes.

Implemented in:

- `backend/app/attention.py`
- `backend/app/main.py`

Acceptance criteria:

- Every autonomous candidate receives an attention outcome.
- The system records why additional information was requested or declined.
- Attention selection remains separate from trade selection.

### Phase 5 — Consequence and counterfactual engine — implemented

Evaluate the consequences of available actions before policy selection.

Implement evaluations for:

- `DO NOTHING`.
- `WAIT`.
- `BUY YES`.
- `BUY NO`.
- Paper or shadow execution alternatives.

Each action evaluation should include:

- Expected utility.
- Outcome distribution.
- Probability of loss or failure.
- Tail risk.
- Fees, slippage, and fill probability.
- Liquidity impact.
- Time-to-resolution.
- Portfolio and correlation effects.
- Opportunity cost.
- Reversibility.

Persist:

- Candidate action evaluations.
- Selected action rationale.
- Counterfactual predictions for rejected alternatives.
- Model-dependent confidence labels.

Implemented in:

- `backend/app/consequence.py`
- `backend/app/models.py`
- `backend/app/eda.py`

Acceptance criteria:

- Every trade has a recorded comparison against `DO NOTHING`.
- Counterfactuals are labeled as estimates, not causal facts.
- Execution costs and realistic paper fills are included.

### Phase 6 — Policy-layer integration — implemented

Separate forecasting, consequence evaluation, policy selection, risk authorization, and execution.

Implement:

- Explicit policy service interface.
- Deterministic policy version `deterministic_policy_v1`.
- Policy input containing belief state and action evaluations.
- Policy output containing proposed action and rationale.
- Risk authorization as a separate final step.
- Rejection, capping, delay, and confirmation outcomes.
- Policy and risk-policy version persistence.

Implemented in:

- `backend/app/policy.py`
- `backend/app/main.py`

Acceptance criteria:

- Forecast models cannot directly create exposure.
- Policy selection cannot bypass risk constraints.
- Existing paper, shadow, and live behavior remains fail-closed.

### Phase 7 — Execution and reconciliation provenance — implemented

Connect the complete order lifecycle to its decision episode.

Implement:

- Episode-linked execution requests.
- Idempotency keys.
- Risk approval snapshots.
- Submission attempts.
- Venue acknowledgements.
- Partial fills.
- Cancellation.
- Reconciliation results.
- Internal/external state mismatches.
- Execution latency and failure reasons.

Acceptance criteria:

- Every execution event has an episode ID.
- Venue acknowledgement is never treated as proof of final execution.
- Reconciliation failures create explicit episode events and operational alerts.

Implemented in:

- `backend/app/memory.py`
- `backend/app/live_execution.py`
- `backend/app/settlement.py`
- `backend/migrations/0006_execution_integrity.sql`

The existing immutable execution ledger, order state transitions, submission
attempts, venue fills, cancellations, and reconciliation results now emit
episode-linked canonical events with deterministic event IDs.

### Phase 8 — Outcome evaluation and attribution — implemented

Evaluate process quality separately from P&L.

Implement attribution across:

- Forecast error.
- Calibration error.
- Consequence-model error.
- Policy error.
- Risk-sizing error.
- Execution error.
- Data-quality error.
- Environmental shock.
- Ordinary variance or luck.

Add post-settlement metrics for:

- Predicted versus realized outcome.
- Decision quality.
- Counterfactual regret.
- CLV.
- Expected versus realized execution.
- Constraint adherence.

Likely files:

- `backend/app/attribution.py`
- `backend/app/settlement.py`
- `backend/app/metrics.py`

Implemented in:

- `backend/app/attribution.py`
- `backend/app/settlement.py`
- `backend/tests/test_eda_attribution.py`

Settlement now stores observational attribution for forecast, calibration,
consequence, policy, sizing, execution, data quality, environmental shock,
and ordinary variance, plus counterfactual utility comparisons. Attribution
is published as an append-only EDA event and does not automatically alter
risk policy.

Acceptance criteria:

- A profitable decision can still be classified as poor process.
- A losing decision can be classified as good process when it was calibrated and risk-compliant.
- Every settled episode receives an attribution record.

### Phase 9 — Expanded experiential memory — implemented (core)

Extend current scars and principles into a complete memory model.

Memory tiers:

- Episodic: complete immutable episodes.
- Semantic: generalized market and regime facts.
- Procedural: validated operating rules.
- Failure: recurring incidents and postmortems.
- Model: versioned model and policy performance.

Implement:

- Structured postmortems.
- Relevance-ranked memory retrieval.
- Provenance links from retrieved memories to source episodes.
- Memory contamination protections.
- Clear separation between evidence and generated explanations.

Acceptance criteria:

- Retrieved memory can influence a new decision without altering historical episodes.
- Scar and principle updates are linked to measurable evidence.

Implemented in `backend/app/experiential_memory.py`, `backend/app/memory.py`,
and settlement postmortem persistence. Retrieval is relevance-ranked and each
postmortem carries episode evidence links.

### Phase 10 — True point-in-time replay — implemented

Turn replay from stored-snapshot retrieval into historical process reconstruction.

Implement:

1. Load observations available before the historical decision timestamp.
2. Reconstruct the belief state.
3. Load the historical model, policy, risk-policy, and configuration versions.
4. Recreate candidate actions.
5. Re-run consequence evaluation and risk authorization.
6. Simulate realistic execution, fees, slippage, and fills.
7. Compare replay output with the original decision.
8. Reject any information that was unavailable at the historical timestamp.

Replay modes:

- Historical reconstruction.
- Current-policy replay.
- Candidate-policy comparison.
- Stress replay.

Likely files:

- `backend/app/replay_engine.py`
- `backend/app/research_validation.py`
- `backend/migrations/0012_eda_replay_runs.sql`

Acceptance criteria:

- Replay prevents look-ahead leakage.
- Incumbent and candidate policies run against identical historical information.
- Replay mismatches are observable and explainable.

Implemented in `backend/app/replay_engine.py`, `backend/app/main.py`, and
`backend/migrations/0011_eda_experiential.sql`. Historical replay requires a
canonical pre-boundary market observation and uses the decision-time objective
snapshot; current-policy replay uses the current persisted objective policy.

### Phase 11 — Model and policy registry — implemented

Create controlled candidate learning and promotion.

Registry records should include:

- Version.
- Training data range.
- Feature version.
- Dependencies.
- Calibration metrics.
- Out-of-sample metrics.
- Regime-specific metrics.
- Known limitations.
- Approval status.
- Rollback target.

Promotion pipeline:

```text
Resolved episodes
    -> evaluation
    -> candidate model or policy
    -> point-in-time replay
    -> out-of-sample validation
    -> stress testing
    -> shadow deployment
    -> controlled canary
    -> operator approval
    -> promotion
```

Rules:

- Never update an active model in place.
- Require minimum sample sizes.
- Compare candidate and incumbent on identical windows.
- Require rollback capability.
- Record the approving operator or automated gate.

Acceptance criteria:

- No candidate can become active without passing statistical, risk, execution, and operational gates.

Implemented in `backend/app/model_registry.py`, `backend/app/main.py`, and
the registry table in `0011_eda_experiential.sql`. Promotion is fail-closed,
requires independently supplied numeric evidence, minimum samples, all gates,
rollback metadata, and an approving operator.

### Phase 12 — Opportunity-universe tracking — implemented (core)

Persist eligible opportunities regardless of whether they become trades.

Track:

- Discovered.
- Analyzed.
- Skipped.
- Delayed.
- Escalated.
- Rejected for missing evidence.
- Rejected for liquidity.
- Rejected for risk.
- Selected for paper exposure.

Implement:

- Opportunity observation schema.
- Candidate-market persistence.
- No-trade reason taxonomy.
- Research reports that include acted-on and rejected opportunities.

Acceptance criteria:

- Research does not evaluate only selected trades.
- Selection bias and survivorship bias are measurable.

Implemented in `OpportunityObservation`, `TradingMemory.save_opportunity`,
and the `/eda/opportunities` endpoints.

### Phase 13 — Observability and operator controls — implemented (core)

Expose the complete EDA loop in telemetry and the control room.

Monitor:

- Episode counts by lifecycle stage.
- Attention decisions.
- Information requests.
- Risk rejections.
- Counterfactual regret.
- Calibration by regime and horizon.
- Attribution categories.
- Replay mismatches.
- Model drift.
- Policy drift.
- Memory retrieval patterns.
- Episode reconstruction failures.

Add operator views for:

- Episode timeline.
- Observation and belief state.
- Candidate actions.
- Risk decision.
- Execution timeline.
- Outcome attribution.
- Replay comparison.
- Model-version comparison.
- Promotion status.

Acceptance criteria:

- Infrastructure failures are distinguishable from model failures.
- Operators can inspect why an action was selected, rejected, delayed, or settled.

Implemented through episode timeline enrichment, information-request outcome
events, model inspection, `/eda/health` integrity reporting, Prometheus
telemetry, operator-scoped EDA mutations, and live-readiness blocking on EDA
hash/orphan-event failures. Observability counters, gauges, histogram samples,
and error samples are also persisted in PostgreSQL migration `0012`.

### Phase 14 — Verification and staged rollout — implemented (core)

Add verification before increasing autonomy.

Test layers:

- Unit tests for each EDA service.
- Schema and migration tests.
- Event immutability tests.
- Point-in-time leakage tests.
- Counterfactual determinism tests.
- Risk-authority tests.
- Execution reconciliation tests.
- Replay parity tests.
- Walk-forward research tests.
- Failure-injection tests.
- Security and authorization tests.

Rollout order:

1. Paper mode only.
2. Shadow evaluation.
3. Controlled-account testing.
4. Canary deployment with hard capital limits.
5. Production only after existing statistical and operational gates pass.

Do not introduce online reinforcement learning until replay, attribution, and promotion are proven reliable.

Focused unit coverage now includes consequence alternatives, attribution,
replay look-ahead exclusion, and fail-closed promotion. PostgreSQL mutation,
migration, and failure-injection suites remain environment-gated and should be
run before any deployment-stage change. Release candidates additionally
require explicit EDA test and replay-parity evidence.

## Recommended implementation batches

Implement the remaining work in these batches:

1. Phases 7–9: execution provenance, attribution, and expanded memory.
2. Phases 10–12: replay, model promotion, and opportunity-universe tracking.
3. Phases 13–14: observability, verification, and staged rollout.

Each batch should include its migration, unit tests, integration tests where applicable, documentation, and a paper-mode verification run before the next batch begins.

## Non-negotiable safety rules

- Live trading remains disabled by default.
- EDA cannot authorize capital independently.
- Missing evidence produces a no-trade or escalation outcome.
- Historical episodes and events are append-only.
- Candidate learning is separated from production promotion.
- Every model, policy, risk policy, and configuration change is versioned.
- No single profitable or losing outcome can rewrite production behavior.
