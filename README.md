# Vesper Trading

Paper-first multi-strategy decision and portfolio-risk agent. It starts with a reference-class probability, measures edge and process quality, applies liquidity, capacity, correlation, and risk gates, and records every decision. Failures create scars and rules that tighten later decisions.

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

Optional semantic-memory setup:

Sibyl is not required for persistence or paper-mode learning. Postgres is the source of truth; enable semantic memory only if you later need retrieval beyond the structured scar and principle records.

## Modes

- `paper`: full decision path, no capital risk; default.
- `shadow`: live signals, no execution.
- `live`: intentionally blocked until a future operator gate and real CLOB execution adapter are enabled.

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

The application database is Supabase Postgres. Configure `DATABASE_URL` with the Supavisor session-mode connection string; no local SQLite volume is used.

Docker starts both the API and the continuous ingestion worker. The worker persists raw market snapshots and keeps collecting until the readiness threshold is met. Check `/pipeline/status` to see current data sufficiency. Reaching the threshold may make the system eligible for the next evaluation stage, but it never enables live capital automatically.

## API

`/decide`, `/markets`, `/markets/{market_id}`, `/markets/book/{token_id}`, `/signals`, `/strategies`, `/state/hot`, `/operations`, `/risk`, `/graph`, `/audit`, `/replay/{decision_id}`, `/scars`, `/principles`, `/decisions`, `/metrics`, `/outcomes`, `/mode/{paper|shadow|live}`, `/operator/request-live`, `/operator/revoke-live`, `/pipeline/status`, `/pipeline/observations`, `/ready`, `/readiness`, `/demo/clear-learning`, and `/health`.

Live Polymarket CLOB execution, Gamma/WebSocket ingestion, and live-capital enablement are isolated as next-stage adapters. Do not put a private key in `backend/.env` for paper mode.

`/ready` reports explicit API, memory, data-quality, freshness, and live-safety checks. Live mode additionally requires a configured `OPERATOR_APPROVAL_CODE`, a successful `/operator/request-live`, positive capital/order limits, and `LIVE_TRADING_ENABLED=true`. Revoke approval with `/operator/revoke-live`.

For production operations, review [SECURITY.md](SECURITY.md), run the paper/shadow gates, and use `deploy/backup.sh` for the persistent memory volume.

## Polymarket data and go-live gates

`/markets` reads public Gamma market discovery and `/markets/book/{token_id}` reads public CLOB book data. Public market data does not require credentials. The official Python v2 CLOB client is an optional dependency for authenticated order workflows; Polymarket trading uses Polygon chain ID 137 and L2 API credentials for authenticated orders. Do not enable live mode until the operator has verified wallet/funder settings, limits, allowances, and a positive paper sample. `LIVE_TRADING_ENABLED` defaults to `false` and live decisions are blocked while it is false.

Every negative outcome can be posted to `/outcomes`; the system updates CLV, expectancy, decision quality, and creates a scar and principle for negative process results.

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

The safe core is implemented: continuous market snapshot ingestion, persistent local storage, reference-class edge estimation, scar-adjusted trust, cooldowns, toxic-flow and capacity gates, kill switches, bucket suspension, paper/shadow adapters, process metrics, replay, and a dashboard that shows the decision gates.

Live Polymarket order submission is not represented as complete. It requires authenticated operator credentials, a verified signer/funder configuration, allowance checks, order lifecycle reconciliation, monitoring, and an explicit production approval. The live adapter therefore fails closed until that integration is completed and tested against a controlled account. Postgres is authoritative; Sibyl is optional and outside the critical learning path.
# vesper-trading
