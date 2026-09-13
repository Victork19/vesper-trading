# Data-quality investigation

## Finding

The displayed score was not measuring the quality of the executable trading
universe. It was measuring the lifetime ratio of valid rows across every
market returned by Gamma. That included discovery-only markets that cannot be
used by this YES/NO CLOB pipeline, plus stale historical failures that never
left the denominator.

There was also a universe mismatch: the ordinary ingestion tick requested the
first broad active-market page, while the autonomous research loop requested
markets resolving within the configured fast-market horizon. The ordinary page
was dominated by long-dated markets, including the political markets visible
in the dashboard, so its book coverage was not representative of the research
universe.

Polymarket exposes market outcomes and CLOB token IDs as aligned arrays, and
the CLOB is only applicable to markets with an enabled order book. The
pipeline therefore needs to distinguish an ineligible market from an eligible
market whose book or payload is bad. See the [Polymarket market-data
overview](https://docs.polymarket.com/market-data/overview) and the [official
list-markets API reference](https://docs.polymarket.com/api-reference/markets/list-markets).

## Evidence from the EC2 logs

- The worker previously aborted a complete ingestion tick on one market with
  `market must provide explicit YES and NO outcome labels`.
- After that was made fail-safe, the worker reported many
  `missing_token_pair` markets. These are not executable binary YES/NO inputs.
- The worker also reported `market_quality quality=0.750`, which is the
  per-market quality penalty for an unavailable or unusable executable book.
- Other markets were skipped for `type_cap` and `cooldown`; those are policy
  controls, not data corruption.
- The later logs showed successful book requests and successful paper
  evaluations. The zero exposed decisions were caused by `DO NOTHING` and
  negative edge, not by dashboard/API transport failure.

## Changes

1. Added explicit binary token-pair parsing. Only markets with matching
   `Yes`/`No` (or `True`/`False`) outcome labels are eligible for executable
   quality scoring.
2. Marked `enableOrderBook=false` and unsupported outcome layouts as
   `eligible=false`, rather than counting them as invalid executable data.
3. Changed the quality score to a rolling window, defaulting to 30 minutes,
   so old incidents do not permanently depress the score.
4. Added `ineligible_observations`, `observed_snapshots`,
   `quality_window_seconds`, and invalid-reason details to the observability
   payload.
5. Avoided CLOB requests for markets whose order book is disabled.
6. Added migration `0013_ingestion_quality_scope.sql` and documented
   `DATA_QUALITY_WINDOW_SECONDS`.
7. Made normal ingestion use the same fast-market time window as the research
   loop when `FAST_MARKETS_ONLY=true` (the default), while preserving an
   explicit opt-out.

The score still penalizes an eligible market with a missing/invalid book. That
is intentional: those markets are executable candidates, so their data must
be complete before they contribute evidence.

## Rollout

On EC2, after pushing these files:

```bash
git pull
docker compose up -d --build --force-recreate trading pipeline reconciler
docker compose ps
docker compose logs pipeline --since=10m | grep -E 'ingestion tick|autonomous paper|ERROR'
```

The migration runs automatically at container startup. The new score needs
fresh observations in the rolling window; allow one or two ingestion ticks,
and up to 30 minutes for old rows to age out.

To inspect the new breakdown without exposing the key in shell history:

```bash
KEY="$(docker compose exec -T trading printenv VESPER_API_KEY | tr -d '\r\n')"
curl -sS -H "X-Vesper-Key: $KEY" \
  https://vesper-trading.duckdns.org/observability | jq '.ingestion'
unset KEY
```

The important fields are `score`, `snapshots`, `observed_snapshots`,
`ineligible_observations`, `invalid_reasons`, and `book_coverage`.
