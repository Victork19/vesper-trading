# Vesper Trading

## System showcase

Vesper Trading is a paper-first, multi-strategy prediction-market decision and risk platform. It combines calibrated probability estimation, executable quote validation, portfolio controls, durable order lifecycle records, operational telemetry, and memory-driven learning.

Its governing principle is simple:

> Risk is earned, not assumed.

Every decision is reconstructible. Every outcome is recorded. Negative process results create scars and tighter future constraints.

## Overall grade

### System-wide grade: **8.1 / 10**

Vesper is a strong paper/shadow trading platform and a credible foundation for controlled deployment. The overall score is reduced by unfinished real-money execution, distributed security state, and incomplete automated testing—not by the core decision architecture.

| Capability | Grade |
|---|---:|
| Trading logic and probability semantics | 9.1/10 |
| Risk sizing and portfolio controls | 8.5/10 |
| Settlement and outcome integrity | 8.5/10 |
| Market-data quality | 9.4/10 |
| Persistence and reliability | 8.0/10 |
| Order lifecycle architecture | 8.0/10 |
| Security and API controls | 8.8/10 |
| Frontend completeness | 9.0/10 |
| Deployment and operations | 9.0/10 |
| Observability and monitoring | 8.8/10 |
| Testing and verification | 7.0/10 |
| Live trading readiness | 4.0/10 |

## Core trading intelligence

- Reference-class probability estimation.
- Logit-space signal aggregation.
- Historical outcome calibration and probability shrinkage.
- Separate YES and NO contract edge calculations.
- Correct prediction-market execution semantics: favorable YES and NO opportunities are both buys of the relevant contract.
- Fee-adjusted and slippage-adjusted executable prices.
- Confidence and uncertainty estimates.
- Strategy-specific minimum edge and maximum size policies.
- Half-Kelly sizing with trust, capacity, correlation, liquidity, and portfolio-heat limits.
- Long-horizon discounts.
- Toxic-flow detection using imbalance, large-wallet signals, and volume/liquidity spikes.
- Evidence-completeness gates.
- Stale-input, inactive-market, low-quality, and untrusted-source gates.
- Explicit `DO NOTHING` outcome.

## Risk and learning system

Vesper maintains several memory tiers:

- **HOT:** current mode, trust, portfolio heat, active constraints, PnL, and correlation regime.
- **WARM:** scars, principles, process snapshots, and experience graph edges.
- **COLD:** decisions, outcomes, execution events, and audit records.
- **REFERENCE:** constitutional rules and operator mandates.

Risk controls include:

- Daily and weekly kill switches.
- Portfolio heat cap.
- Strategy trust decay.
- Scar-adjusted trust.
- Bucket suspension after negative expectancy.
- Constitutional scar stops.
- Cooldown periods.
- Process-quality and rule-adherence metrics.
- Durable outcome settlement with duplicate-settlement protection.
- Contract-based PnL calculation when the resolved market result is known.

## Market-data quality

The market-data subsystem provides:

- Gamma market discovery and metadata validation.
- CLOB order-book retrieval.
- Bounded timeouts and retry with exponential backoff.
- Normalized bid/ask levels.
- Best bid and best ask extraction.
- Depth aggregation by price level.
- Crossed and locked book rejection.
- Price, size, liquidity, and volume validation.
- Active/closed market checks.
- Stable market identifiers.
- Freshness timestamps and stale-data rejection.
- Source attribution.
- Per-observation quality scoring.
- Continuous book fetching during ingestion.
- Book sequence capture.
- Separate immutable snapshot deduplication and freshness observation tracking.
- Quality diagnostics through:

```text
GET /markets/quality/{market_id}?token_id={token_id}
```

Shadow and live decisions require trusted, fresh executable quotes.

## Order lifecycle

Every executable paper or shadow decision can create a durable order record containing:

- Order ID.
- Client order ID.
- Decision ID.
- Market ID.
- Side.
- Requested size.
- Limit price.
- Status.
- Filled size.
- Average fill price.
- Venue order ID.
- Error state.
- Creation and update timestamps.

Supported lifecycle states include:

```text
new
accepted
partially_filled
filled
canceled
rejected
failed
```

Available endpoints:

```text
GET /orders
GET /orders/{order_id}
```

Paper execution records simulated fills. Shadow execution records accepted signal orders without capital exposure. Live execution remains fail-closed until authenticated order submission and reconciliation are implemented.

## Persistence and reliability

- SQLite WAL mode.
- `FULL` synchronous durability.
- Busy timeouts.
- Foreign-key enforcement.
- Thread-safe write locks.
- Durable journal events.
- Durable orders table.
- Durable market snapshots.
- Per-tick market observations.
- Schema creation and additive migration checks.
- Backup procedure that stops both API and ingestion writers.
- Persistent ingestion-worker heartbeat state.
- Last-success and last-error tracking.

## Security and API controls

The API supports scoped credentials:

- **Read:** dashboards, history, metrics, and diagnostics.
- **Trade:** read access plus decision and outcome submission.
- **Admin:** operator controls, mode changes, key rotation, and key revocation.

Security features include:

- `X-Vesper-Key` authentication.
- SHA-256 key storage in process memory rather than plaintext lookup tables.
- Scope enforcement.
- Constant API-boundary checks.
- Per-key and per-route rate limiting.
- `429` responses with `Retry-After`.
- Admin-only key rotation.
- Admin-only key revocation.
- Protected state-changing endpoints.
- Protected operational and sensitive read endpoints.
- Request correlation IDs.
- No private key exposure to the language-model decision layer.

Credential endpoints:

```text
POST /operator/rotate-key?scope=read|trade
POST /operator/revoke-key/{key_id}
```

Production deployments should persist rotated keys in a dedicated secret manager and never expose admin credentials in public frontend builds.

## Frontend control room

The React/Vite frontend provides authenticated operational views for:

- Decisions.
- Orders and lifecycle status.
- Process metrics.
- Scars and rules.
- Operations and active alerts.
- Portfolio heat.
- Market-data quality.
- Worker health.
- Recent error counts.
- Pipeline state.
- Paper decision evaluation.
- Refresh and telemetry polling.

The frontend uses:

- `VITE_API_URL` for API routing.
- `VITE_API_KEY` for client access.
- `VITE_ADMIN_KEY` only for protected internal operator builds.

## Observability and monitoring

Vesper exposes:

```text
GET /observability
GET /alerts
GET /metrics/prometheus
```

Telemetry includes:

- HTTP request counts.
- HTTP status distribution.
- Request latency histograms.
- Error counters.
- Decision counts by action, strategy, and mode.
- Outcome counts.
- Order counts by mode and lifecycle status.
- Market-data request failures.
- Crossed-book failures.
- Book depth gauges.
- Ingestion observation counts.
- Valid versus invalid observation counts.
- Worker heartbeat freshness.
- Worker error count.
- Portfolio heat.
- Daily and weekly PnL.
- Active alert conditions.

Operational alerts cover:

- Stale market data.
- Low market-data quality.
- Stale ingestion worker.
- Error bursts.

## Deployment and operations

The Docker deployment includes:

- FastAPI trading API.
- Continuous ingestion worker.
- Nginx reverse proxy.
- Certbot certificate renewal.
- Persistent trading-data volume.
- Health checks.
- Read-only application filesystems.
- Non-root application containers.
- Dropped Linux capabilities.
- `no-new-privileges`.
- CPU and memory limits.
- Temporary filesystem isolation.
- HTTPS configuration.
- Security response headers.
- Request IDs through the reverse proxy.
- Backup and restore workflow documentation.

Release and incident tooling includes:

- GitHub Actions CI for backend compilation, pytest, and frontend build.
- Deterministic test environment configuration.
- `deploy/validate.sh` deployment smoke validation.
- `deploy/RUNBOOK.md` health, incident, key-rotation, and backup procedures.

## Operating modes

### Paper

Full decision and risk path with simulated execution and zero capital exposure.

### Shadow

Live market signals and executable quote requirements without capital execution.

### Live

Intentionally fail-closed. Requires operator approval, authentication, capital/order limits, sample-size validation, fresh high-quality market data, and a complete authenticated order/reconciliation adapter.

## API surface

Core endpoints include:

```text
GET  /health
GET  /ready
GET  /state/hot
GET  /constitution
GET  /strategies
GET  /decisions
GET  /orders
GET  /orders/{order_id}
GET  /scars
GET  /principles
GET  /metrics
GET  /audit
GET  /risk
GET  /dashboard
GET  /markets
GET  /markets/{market_id}
GET  /markets/book/{token_id}
GET  /markets/quality/{market_id}
GET  /pipeline/status
GET  /pipeline/observations
GET  /observability
GET  /alerts
GET  /metrics/prometheus
POST /decide
POST /outcomes
POST /signals
POST /mode/{mode}
POST /operator/request-live
POST /operator/revoke-live
POST /operator/rotate-key
POST /operator/revoke-key/{key_id}
```

## Current boundary

Vesper is ready for serious paper and shadow operation. It is not yet approved for unattended real-money trading because the final live layer still requires:

- Authenticated CLOB order submission.
- Signer and funder verification.
- Allowance checks.
- Partial-fill reconciliation.
- Cancellation and retry policy.
- Balance reconciliation.
- Exchange outage handling.
- Independent emergency kill switch.
- Distributed credential and rate-limit state.
- External Prometheus/Alertmanager deployment.
- Full pytest execution in CI.

The system is deliberately safer because these limitations are explicit, observable, and fail closed.
