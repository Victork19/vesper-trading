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

Readiness uses independent exposed outcomes, keyed by strategy, market and regime. Repeated evaluations of the same unresolved market do not count as new research evidence. A Polymarket paper evaluation without a reference rate, signal, or resolved reference-class history is recorded as a no-trade diagnostic with `reference_evidence_required`; the system never turns a missing model into a synthetic 50% probability.

Recommended backend settings:

```env
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-REGION.pooler.supabase.com:5432/postgres
AUTO_PAPER_ENABLED=true
AUTO_PAPER_DECISIONS_PER_TICK=3
AUTO_PAPER_MARKET_COOLDOWN_SECONDS=21600
AUTO_PAPER_MAX_PER_TYPE_PER_TICK=1
AUTO_PAPER_STRATEGY=reference_class
```

## API

`/decide`, `/markets`, `/markets/{market_id}`, `/markets/input/{market_id}`, `/markets/book/{token_id}`, `/markets/quality/{market_id}`, `/signals`, `/strategies`, `/state/hot`, `/operations`, `/risk`, `/graph`, `/audit`, `/replay/{decision_id}`, `/scars`, `/principles`, `/decisions`, `/metrics`, `/outcomes`, `/research/report`, `/mode/{paper|shadow|live}`, `/operator/request-live`, `/operator/revoke-live`, `/pipeline/status`, `/pipeline/observations`, `/ready`, `/readiness`, `/readiness/summary`, `/observability`, `/alerts`, `/metrics/prometheus`, `/demo/clear-learning`, and `/health`.

The frontend uses `/markets/input/{market_id}` before evaluation to obtain a fresh normalized market and book snapshot. Decisions retain source, quality score, quote timestamp, book sequence and snapshot hash. Do not put a private key in `backend/.env` for paper mode.

`/ready` reports explicit API, memory, data-quality, freshness, and live-safety checks. Live mode additionally requires a configured `OPERATOR_APPROVAL_CODE`, a successful `/operator/request-live`, positive capital/order limits, and `LIVE_TRADING_ENABLED=true`. Revoke approval with `/operator/revoke-live`.

For production operations, review [SECURITY.md](SECURITY.md), run the paper/shadow gates, and use `deploy/backup.sh` for Supabase Postgres backups. The frontend overview includes an autonomous-readiness summary showing decisions, resolved outcomes, win rate, data quality, sample progress and live blockers.

Session-authenticated state-changing requests are origin-checked and API-key/session requests are rate-limited per principal and route. Keep `CORS_ORIGINS` restricted to the deployed frontend origin.

## Polymarket data and go-live gates

`/markets` reads public Gamma market discovery and `/markets/book/{token_id}` reads public CLOB book data. Public market data does not require credentials. The official Python v2 CLOB client is an optional dependency for authenticated order workflows; Polymarket trading uses Polygon chain ID 137 and L2 API credentials for authenticated orders. Do not enable live mode until the operator has verified wallet/funder settings, limits, allowances, and a positive paper sample. `LIVE_TRADING_ENABLED` defaults to `false` and live decisions are blocked while it is false.

Every negative outcome can be posted to `/outcomes`; the system updates CLV, expectancy, decision quality, and creates a scar and principle for negative process results.

`/research/report` produces a chronological 70/30 train/out-of-sample report once at least ten exposed outcomes exist. It reports win rate, expectancy, profit factor, drawdown, CLV, Brier score, independent buckets, and the observed post-Scar results. It is a research diagnostic, not a guarantee of future profitability.

The continuous pipeline also resolves eligible paper decisions automatically. On each ingestion tick it checks pending
decisions against their Polymarket market IDs and settles only markets that are closed/resolved with an unambiguous binary
`1/0` outcome price. Automatic settlements update PnL, trust, process metrics, scars, principles, and the audit journal in
the same path as manual `/outcomes` submissions. Configure `RESOLUTION_BATCH_SIZE` to control the maximum number checked
per pipeline tick (default `25`). Ambiguous, unresolved, manual, or unavailable markets remain pending.

With `AUTO_PAPER_ENABLED=true` (the default), the pipeline also evaluates a small rotating set of liquid markets in paper
mode. It fetches a fresh CLOB book, avoids recently evaluated markets, records no-trade evaluations without fabricating edge,
and only creates paper exposure when the configured evidence produces a genuine edge. Tune it with
`AUTO_PAPER_DECISIONS_PER_TICK`, `AUTO_PAPER_MARKET_COOLDOWN_SECONDS`, and `AUTO_PAPER_MAX_PER_TYPE_PER_TICK`.

## Current implementation boundary

The safe core is implemented: continuous Gamma/CLOB market ingestion, Supabase Postgres persistence, autonomous paper evaluation, automatic terminal resolution, exact quote/book provenance, reference-class edge estimation, scar-adjusted trust, cooldowns, toxic-flow and capacity gates, kill switches, bucket suspension, paper/shadow adapters, process metrics, calibration metrics, replay, audit events, Prometheus telemetry, and a readiness dashboard.

Live Polymarket order submission is not complete. It requires authenticated operator credentials, verified signer/funder configuration, allowance checks, idempotent order submission, partial-fill handling, cancellation, venue-state reconciliation, monitoring and explicit production approval. The live adapter therefore fails closed until that integration is completed and tested against a controlled account. Postgres is authoritative and Sibyl is outside the critical learning path.
