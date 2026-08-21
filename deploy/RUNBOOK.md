# Vesper operations runbook

## Health

```bash
curl "$VESPER_API_URL/health"
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/ready"
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/observability"
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/alerts"
```

## Backend verification

Run the full backend suite only against a disposable Postgres database. Never point tests at the production Supabase URL. If `VESPER_TEST_DATABASE_URL` is omitted, the script creates and removes a disposable Postgres 16 container automatically:

```bash
bash deploy/test-backend.sh
```

The test runner refuses Supabase URLs, waits for the disposable database to accept connections, and builds a separate image containing the test suite. Production containers do not include test files.

## Incident response

1. If `MARKET_DATA_STALE` or `INGESTION_WORKER_STALE` is active, keep the system in paper/shadow and inspect the pipeline logs.
2. If `ERROR_BURST` is active, revoke the client key and switch the operator mode to paper.
3. If execution state is uncertain, revoke live approval and do not retry orders manually until `/orders` and venue state agree.
4. Run `deploy/backup.sh` before destructive recovery work.
5. Validate the deployment with `deploy/validate.sh` after recovery.

## Key rotation

Rotate with the admin credential:

```bash
curl -X POST -H "X-Vesper-Key: $VESPER_ADMIN_KEY" \
  "$VESPER_API_URL/operator/rotate-key?scope=trade"
```

Store the returned key in the secret manager, replace the configured client key, and restart the API and frontend. Revoke the old key after clients have migrated.

## Backups

Back up before migrations or recovery. The backup script runs `pg_dump` against the Supabase Postgres database. Copy resulting dump files off-host and test restoration regularly.
