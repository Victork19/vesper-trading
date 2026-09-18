# Controlled deployment runbook

Vesper is paper/shadow-only by default. A deployment stage is an operational
control, not evidence that the strategy is profitable.

## Stages

- `paper`: simulated execution only. This is the default and the safe local mode.
- `shadow`: live market data and decision generation, with no venue submission.
- `controlled`: controlled-account integration testing. Use a dedicated account,
  a small balance, and an explicit operator approval. Do not use production funds.
- `canary`: limited capital deployment. Set `CANARY_MAX_CAPITAL` below
  `MAX_LIVE_CAPITAL`; the reservation layer enforces the lower limit.
- `production`: only after the controlled-account, reconciliation, recovery,
  security, and statistical gates have passed independently.

## Required release sequence

1. Keep `VESPER_LIVE_HARD_LOCK=true`, `LIVE_TRADING_ENABLED=false`, and
   `VESPER_DEPLOYMENT_STAGE=paper` while migrating and validating the database.
2. Run the full backend test suite in an environment with PostgreSQL and the
   pinned venue SDK installed. The test suite must include database-backed
   idempotency, crash recovery, partial-fill, cancellation-race, balance
   mismatch, CSRF, and controlled-account tests.
   Use the verification command below. Database tests are intentionally
   opt-in and must point at a disposable test database, never an application
   production URL.
3. Deploy `shadow` and verify ingestion freshness, token identity, quote age,
   research eligibility, audit events, alerts, backups, and worker heartbeats.
4. Use `controlled` only with a dedicated wallet/account. Verify signer, funder,
   chain, exchange contract, token allowances, collateral, account balance, and
   internal reservations. Execute a known small order, partial fill, cancel/fill
   race, timeout, restart, and kill-switch scenario.
5. Verify that every external order is represented by a durable intent and that
   every fill, cancellation, settlement, and balance observation is reconciled.
   Any `UNKNOWN`, `UNCERTAIN`, or balance mismatch blocks promotion.
6. Promote to `canary` only with positive out-of-sample evidence, a completed
   review by two authorized operators, and a pre-declared capital limit. Monitor
   reconciliation, reserved capital, account collateral, slippage, fill drift,
   and model diagnostics continuously.
7. Promote to `production` only through a reviewed configuration change. Never
   remove the hard lock or increase capital as an incident response.

## Verification commands

Run the static/unit suite first:

```bash
cd backend
python -m pytest -q -m 'not integration'
```

Run PostgreSQL and controlled integration tests only against a disposable
database:

```bash
export VESPER_RUN_DB_TESTS=1
export VESPER_TEST_DATABASE_URL='postgresql://.../vesper_verification'
python -m pytest -q
```

The test suite skips database tests unless both variables are explicitly set.
Skipped integration tests are not release evidence. A release job must record
these independent evidence markers as `1` only after the corresponding test or
drill has completed:

```text
VESPER_DB_TESTS_PASSED
VESPER_SDK_FIXTURES_PASSED
VESPER_FAULT_TESTS_PASSED
VESPER_CONTROLLED_ACCOUNT_PASSED
VESPER_BACKUP_RESTORE_PASSED
```

The conservative gate can be inspected with:

```bash
python backend/tools/release_gate.py
```

It never enables trading and exits non-zero for a controlled, canary, or
production stage when evidence is missing.

Any failed release gate is a no-go. Do not promote or enable live trading until
the failed check has been independently remediated and rerun.

## Emergency procedure

Use the operator kill switch immediately. It must stop new submissions, cancel
open orders, and keep reconciling until all venue orders are terminal. Do not
release the switch while the venue circuit is open, account verification fails,
or any order remains unresolved. Switching to paper mode does not stop the
reconciler.

## Non-negotiable evidence

Win rate alone is not a release criterion. Require independent market buckets,
chronological out-of-sample evaluation, confidence intervals, calibration,
cost/slippage accounting, live-vs-paper drift measurement, and a complete
execution/account reconciliation. No gate implies or guarantees future profit.
