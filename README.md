# Vesper Trading

Paper-first Polymarket research and portfolio-risk system. It continuously ingests Gamma market metadata and CLOB order books, evaluates liquid markets, applies reference-class probability, edge, liquidity, capacity, toxic-flow, correlation and risk gates, and records reconstructible decisions. Outcomes update calibration, CLV, expectancy, trust, scars and operating principles.

## Run locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Postgres is the source of truth. Sibyl is not required for persistence or paper-mode learning; structured scars, principles, process snapshots, decisions and the audit journal are stored in Supabase Postgres.

## Modes

- `paper`: full decision path, no capital risk; default.
- `shadow`: live signals, no execution.
- `live`: fails closed; authenticated CLOB submission and order reconciliation are not yet production-enabled.

## Memory map

- HOT: trust, portfolio heat, correlation regime, active constraints.
- WARM: scars, principles, process snapshots.
- COLD: decisions and outcome events.
- REFERENCE: hard risk rules and operator mandates.
- ARCHIVE: retired records.

## Docker

```bash
cp backend/.env.example backend/.env
docker compose -f backend/docker-compose.yml up -d --build
curl http://localhost:8000/health
```

The application database is Supabase Postgres. Configure `DATABASE_URL` with the Supavisor session-mode pooler connection string; no local SQLite volume is used.

Docker starts both the API and the continuous pipeline worker. The worker persists raw market snapshots, automatically evaluates qualified paper markets, resolves terminal outcomes, and updates the learning layer. Check `/readiness/summary` for paper-sample progress and the exact live blockers. Reaching a data or sample threshold never enables live capital automatically.

Readiness uses independent exposed outcomes from the currently active model version, keyed by strategy, market and regime. Historical model versions are never pooled into the active model's out-of-sample proof. Repeated evaluations of the same unresolved market do not count as new research evidence. A Polymarket paper evaluation without a reference rate, signal, or resolved reference-class history is recorded as a no-trade diagnostic with `reference_evidence_required`; the system never turns a missing model into a synthetic 50% probability.

Recommended backend settings:

```env
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-REGION.pooler.supabase.com:5432/postgres
AUTO_PAPER_ENABLED=true
AUTO_PAPER_DECISIONS_PER_TICK=3
AUTO_PAPER_EXPLORATION_ENABLED=true
AUTO_PAPER_EXPLORATION_MAX_PER_TICK=2
AUTO_PAPER_EXPLORATION_SIZE=.01
AUTO_PAPER_MARKET_COOLDOWN_SECONDS=21600
AUTO_PAPER_MAX_PER_TYPE_PER_TICK=1
AUTO_PAPER_STRATEGY=reference_class
AUTO_PAPER_MIN_RESOLUTION_HOURS=0.05
AUTO_PAPER_MAX_RESOLUTION_HOURS=24
AUTO_PAPER_PREFER_FAST_MARKETS=true
AUTO_PAPER_MARKET_PAGE_SIZE=100
AUTO_PAPER_MARKET_PAGES=5
PAPER_FEE_RATE=0.02
PAPER_SLIPPAGE_BPS=10
FAST_MODEL_ENABLED=true
FAST_MODEL_LOOKBACK_MINUTES=30
FAST_MODEL_CACHE_SECONDS=15
FAST_MODEL_MIN_CONFIDENCE=0.2
FAST_MODEL_MIN_CALIBRATION_SAMPLES=20
FAST_MODEL_FALLBACK_URL=https://api.exchange.coinbase.com/products
```

## API

`/decide`, `/markets`, `/markets/{market_id}`, `/markets/input/{market_id}`, `/markets/book/{token_id}`, `/markets/quality/{market_id}`, `/signals`, `/strategies`, `/state/hot`, `/operations`, `/risk`, `/graph`, `/audit`, `/replay/{decision_id}`, `/episodes/{episode_id}`, `/episodes/{episode_id}/events`, `/eda/objective`, `/scars`, `/principles`, `/decisions`, `/metrics`, `/outcomes`, `/research/report`, `/mode/{paper|shadow|live}`, `/operator/request-live`, `/operator/revoke-live`, `/pipeline/status`, `/pipeline/observations`, `/ready`, `/readiness`, `/readiness/summary`, `/observability`, `/alerts`, `/metrics/prometheus`, `/demo/clear-learning`, and `/health`.

The EDA foundation records an immutable decision episode for each decision, including the point-in-time market observation, belief state, provenance, candidate action set, selected action, risk gates, model/policy/data versions, execution events, and later resolution events. The objective policy is risk-adjusted expected value; forecasting remains advisory and the existing risk layer retains final authority.

The frontend uses `/markets/input/{market_id}` before evaluation to obtain a fresh normalized market and independent YES/NO book snapshots. Polymarket decisions fail closed unless both contract quotes and both ask-side books are present, and reject snapshots whose contract-book timestamps exceed `MAX_CONTRACT_QUOTE_SKEW_SECONDS`. Decisions retain source, quality score, per-contract quote timestamps, quote skew, book sequence and snapshot hash. Do not put a private key in `backend/.env` for paper mode.

`/ready` reports explicit API, memory, data-quality, freshness, and live-safety checks. Live mode additionally requires a configured `OPERATOR_APPROVAL_CODE`, a successful `/operator/request-live`, positive capital/order limits, `LIVE_TRADING_ENABLED=true`, a sufficient chronological out-of-sample sample, and positive lower confidence bounds for OOS expectancy and Brier/log-loss lift versus the market baseline. Revoke approval with `/operator/revoke-live`.

For production operations, review [SECURITY.md](SECURITY.md) and the [controlled deployment runbook](CONTROLLED_DEPLOYMENT.md), run the paper/shadow gates, and use `deploy/backup.sh` for Supabase Postgres backups. The frontend overview includes an autonomous-readiness summary showing decisions, resolved outcomes, win rate, data quality, sample progress and live blockers.

## Verification and release gates

The verification suite is intentionally conservative. Run the static/unit
tests with:

```bash
cd backend
python -m pytest -q -m 'not integration'
```

Database, concurrency, migration, fault-injection, and controlled-account
tests require an explicit disposable test database:

```bash
export VESPER_RUN_DB_TESTS=1
export VESPER_TEST_DATABASE_URL='postgresql://.../vesper_verification'
python -m pytest -q
```

`DATABASE_URL` is never used by these verification fixtures. A Supabase test
URL additionally requires `VESPER_ALLOW_SUPABASE_TEST_DB=1`. Skipped tests do
not count as passing release evidence. Use `python backend/tools/release_gate.py`
to check the required evidence markers for controlled/canary/production
stages. The gate only reports status; it cannot authorize live trading.

Session-authenticated state-changing requests are origin-checked and API-key/session requests are rate-limited per principal and route. Keep `CORS_ORIGINS` restricted to the deployed frontend origin.

## Polymarket data and go-live gates

`/markets` reads public Gamma market discovery and `/markets/book/{token_id}` reads public CLOB book data. Public market data does not require credentials. The official Python v2 CLOB client is an optional dependency for authenticated order workflows; Polymarket trading uses Polygon chain ID 137 and L2 API credentials for authenticated orders. Do not enable live mode until the operator has verified wallet/funder settings, limits, allowances, and a positive paper sample. `LIVE_TRADING_ENABLED` defaults to `false` and live decisions are blocked while it is false.

Every negative outcome can be posted to `/outcomes`; the system updates CLV, expectancy, decision quality, and creates a scar and principle for negative process results.

Scars are contextual, persistent constraints rather than a simple loss counter. They retain the strategy, market bucket, regime, model version, executable price, confidence, data quality, fill fraction, cost drag, gates and a counterfactual lesson. Repeated failures reinforce one scar and increase its evidence count; qualifying positive outcomes rehabilitate it gradually. Use `/memory/digest` to inspect the ranked lessons that would affect a future decision.

Paper execution uses `paper_microstructure_v1`: it walks the selected contract’s ask levels, computes a reproducible VWAP fill, applies quote quality, configured fees and slippage, and records the simulated execution price plus both observed ask ladders for replay. Missing depth fails closed to zero fill; a paper result is never allowed to assume liquidity that was not observed. If depth impact removes the edge, the paper decision is rejected before exposure is recorded.

Research reports use the active model version's unique strategy/market/regime buckets, exposure-weight repeated observations within each market, a chronological 70/30 split, and an embargo bucket between train and out-of-sample data. Versioned model diagnostics score the persisted calibrated model probability separately from downstream fair-value adjustments; raw model probability remains reserved for calibration fitting. They report calibration bins, Brier score, log loss, calibration error, CLV, expectancy, drawdown, profit factor, cost drag and market counts. These are research diagnostics, not guarantees of profitability.

`/research/report` produces a chronological 70/30 train/out-of-sample report only after at least thirty independent resolved market buckets exist, with a separate out-of-sample sufficiency warning. It reports win rate, expectancy and confidence bounds, profit factor, drawdown, CLV, Brier/log-loss lift confidence bounds, independent buckets, and observed post-Scar results. It is a research diagnostic, not a guarantee of future profitability.

The continuous pipeline also resolves eligible paper decisions automatically. On each ingestion tick it checks pending
decisions against their Polymarket market IDs, fairly rotates through unique pending markets, settles every pending exposure
for a terminal market, and settles only markets that are closed/resolved with an unambiguous binary
`1/0` outcome price. Automatic settlements update PnL, trust, process metrics, scars, principles, and the audit journal in
the same path as manual `/outcomes` submissions. Configure `RESOLUTION_BATCH_SIZE` to control the maximum number checked
per pipeline tick (default `25`). Ambiguous, unresolved, manual, or unavailable markets remain pending.

With `AUTO_PAPER_ENABLED=true` (the default), the pipeline evaluates a small rotating set of liquid markets in paper
mode. It fetches a fresh CLOB book, avoids recently evaluated markets, and prioritizes markets nearest to resolution.
The reference strategy records no-trade evaluations unless a genuine probability edge survives costs. When that strategy
has no edge, the separate `paper_exploration` strategy can place a tiny fixed-size quote-selection sample when
`AUTO_PAPER_EXPLORATION_ENABLED=true` (the default). Exploration positions are explicitly marked
`paper_exploration_v1`, are excluded from research reports, calibration, live-readiness samples and the 100-outcome gate,
but still use real paper fills, settlement, PnL, scars and operational diagnostics. Tune its safety cap with
`AUTO_PAPER_EXPLORATION_MAX_PER_TICK` and `AUTO_PAPER_EXPLORATION_SIZE`; tune discovery with
`AUTO_PAPER_DECISIONS_PER_TICK`, `AUTO_PAPER_MARKET_COOLDOWN_SECONDS`, `AUTO_PAPER_MAX_PER_TYPE_PER_TICK`,
`AUTO_PAPER_MIN_RESOLUTION_HOURS`, `AUTO_PAPER_MAX_RESOLUTION_HOURS`, and `AUTO_PAPER_PREFER_FAST_MARKETS`.
Five-minute markets can increase sample throughput, but they require liquid books and realistic latency/slippage
assumptions.

Paper mode automatically runs a balanced strategy tournament when
`AUTO_PAPER_MULTI_STRATEGY_ENABLED=true` (the default). It assigns eligible markets among the market baseline
(`reference_class`), the independent short-horizon model (`fast_model`), and `hybrid_ensemble`, while allowing only
one strategy to open a paper position on a market at a time. The hybrid combines the baseline and fast forecasts in
log-odds space, records both component forecasts and weights, and adapts its fast-model weight only from resolved,
research-eligible paper outcomes. Exploration remains a separate fixed-size execution experiment and is excluded from
live-readiness evidence.

Fast-only mode is a hard constraint when `FAST_MARKETS_ONLY=true` (the default). `AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS=1`
limits autonomous exposure to markets resolving within one hour. Slower markets are excluded before autonomous evaluation,
and API decisions sourced from Polymarket receive `slow_market_excluded` instead of exposure when they exceed the limit.
Existing slow pending decisions remain monitored for terminal resolution but receive no new exposure. Fast-only research
reports exclude slow-market outcomes.

Ingestion and paper-trading scope are separate. When the short-term window is empty, `INGESTION_FALLBACK_ACTIVE_MARKETS=true`
(the default) lets the data worker collect active markets for dashboard visibility and historical observations. It does not
allow the autonomous paper trader to trade those slower markets. `INGEST_OBSERVATION_SAMPLE_SECONDS=300` also intentionally
limits each market to one sampled observation every five minutes, so a worker can fetch fresh books every minute without
creating duplicate learning rows.

For short-horizon crypto markets, the autonomous loop uses `fast_market_v3`: a conservative, calibratable multi-horizon
drift/volatility estimate from public one-minute spot candles. It uses winsorized returns, EWMA short/medium/long windows,
volatility-shock detection, mean-reversion pressure, horizon projection, uncertainty bounds and regime labels. Calibration
targets the resolved event itself—not whether the selected trade side happened to win—and fails closed when the asset,
direction, price history, freshness or confidence threshold is unavailable. This is a research model that must be
validated against resolved outcomes before any live consideration.

## Current implementation boundary

The safe core is implemented: continuous Gamma/CLOB market ingestion, Supabase Postgres persistence, autonomous paper evaluation, automatic terminal resolution, exact quote/book provenance, reference-class edge estimation, scar-adjusted trust, cooldowns, toxic-flow and capacity gates, kill switches, bucket suspension, paper/shadow adapters, process metrics, calibration metrics, replay, audit events, Prometheus telemetry, and a readiness dashboard.

The live execution foundation is implemented behind a fail-closed service boundary. It includes authenticated CLOB-client integration, signer/funder and balance/allowance readiness checks, deterministic idempotent client order IDs, durable submission attempts, partial-fill reconciliation, cancellation, bounded retry policy, venue circuit breakers, account snapshots, and a durable emergency kill switch. The integration remains disabled until the operator configures a controlled account, completes the controlled-account test suite, and passes the statistical, wallet, venue-health, reconciliation, and approval gates. Postgres is authoritative and Sibyl is outside the critical learning path.
