# Vesper Trading: EC2 + Cloudflare Pages deployment

This guide deploys the Vesper backend and ingestion worker on Ubuntu EC2, with Nginx/Certbot HTTPS, and the React frontend on Cloudflare Pages. The deployment is paper-first: live trading remains disabled.

## Architecture

```text
Cloudflare Pages -> HTTPS -> EC2 Nginx -> trading API :8000
                                      -> PostgreSQL :5432 (private Compose network)
                                      -> ingestion worker
                                      -> scheduled backup -> private S3 bucket
```

Port 8000 must never be exposed publicly.

## 1. AWS prerequisites

Use Ubuntu 22.04/24.04, preferably 2 vCPU, 4 GB RAM, 30 GB gp3 EBS, and an Elastic IP.

Security group:

| Port | Source | Purpose |
|---:|---|---|
| 22 | Operator IP only | SSH |
| 80 | `0.0.0.0/0` | HTTP/ACME challenge |
| 443 | `0.0.0.0/0` | HTTPS |

Do not open port 8000 or any database port.

Point your DNS record, such as `api.example.com`, to the Elastic IP before issuing the certificate.

## 2. Install Docker and clone

```bash
ssh -i /path/to/key.pem ubuntu@EC2_PUBLIC_IP
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git openssl jq unattended-upgrades
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker --version
docker compose version

cd ~
git clone https://github.com/YOUR_ORG/YOUR_REPO.git vesper-trading
cd ~/vesper-trading
```

## 3. Configure backend secrets

```bash
cp backend/.env.example backend/.env
cp backend/backup.env.example backend/backup.env
chmod 600 backend/.env
chmod 600 backend/backup.env
openssl rand -base64 48
openssl rand -base64 48
openssl rand -base64 48
nano backend/.env
nano backend/backup.env
```

Use separate generated values for the client key, admin key, and operator approval code. Never commit `backend/.env` or `backend/backup.env`.

Recommended paper/shadow values:

```dotenv
CORS_ORIGINS=https://YOUR-PAGES-HOST.pages.dev
VESPER_ENV=production
VESPER_COOKIE_SECURE=true
VESPER_DOMAIN=api.example.com
CERTBOT_EMAIL=ops@example.com

POSTGRES_DB=vesper
POSTGRES_USER=vesper
POSTGRES_PASSWORD=long-random-url-safe-password
DATABASE_URL=postgresql://vesper:long-random-url-safe-password@db:5432/vesper
BACKUP_DATABASE_SOURCE=compose
POSTGRES_SERVICE=db
DATABASE_POOL_MAX=4
SIBYL_OFFICIAL=0
TRADING_MODE=paper

POLYMARKET_GAMMA_URL=https://gamma-api.polymarket.com
POLYMARKET_CLOB_URL=https://clob.polymarket.com
PIPELINE_INTERVAL_SECONDS=60
INGEST_MARKET_LIMIT=50
MIN_MARKET_SNAPSHOTS=1000
MIN_DATA_QUALITY=0.95
MARKET_DATA_RETRIES=3
MAX_BOOK_LEVELS=50
INGEST_REQUIRE_BOOKS=true
INGEST_OBSERVATION_SAMPLE_SECONDS=300
RETENTION_RAW_MARKET_DAYS=30
RETENTION_MARKET_EVENT_DAYS=30
RETENTION_METRIC_SAMPLE_DAYS=7
RETENTION_JOURNAL_DAYS=365
RETENTION_CLEANUP_INTERVAL_SECONDS=21600

VESPER_AUTH_REQUIRED=true
VESPER_API_KEY=long-random-client-key
VESPER_ADMIN_KEY=long-random-admin-key
VESPER_SESSION_SECRET=long-random-session-secret
OPERATOR_APPROVAL_CODE=long-random-operator-approval-secret
VESPER_RATE_LIMIT_PER_MINUTE=120

LIVE_TRADING_ENABLED=false
MAX_LIVE_CAPITAL=0
MAX_LIVE_ORDER_SIZE=0
```

The default deployment runs PostgreSQL 16 in Docker on a private Compose
network. Its data is stored in the persistent `postgres_data` Docker volume;
the volume protects against container replacement, not VPS loss. Vesper
persists decisions, EDA episodes/events, market evidence, orders, fills,
accounting, security, configuration history, replay results, model registry
records, opportunities, and durable observability samples in PostgreSQL.

For VPS-loss protection, configure `backend/backup.env` with a private S3
destination. The `backup` Compose service creates a custom-format dump every
15 minutes, uploads the dump and checksum, and keeps only a short local
retention window. Supabase or another managed PostgreSQL service is also
supported: set `BACKUP_DATABASE_SOURCE=url` for the manual `deploy/backup.sh`
path.

### Create the remote S3 destination

In AWS S3, create a private bucket in the same region as the VPS, keep Block
Public Access enabled, and optionally enable versioning and a lifecycle rule
for old backup objects. The destination value is the bucket URI, for example:

```dotenv
BACKUP_S3_URI=s3://my-private-vesper-backups/vesper
AWS_REGION=eu-west-3
```

For credentials, create a dedicated IAM user or role used only for this
prefix. A minimal IAM policy is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::my-private-vesper-backups",
      "Condition": { "StringLike": { "s3:prefix": ["vesper", "vesper/*"] } }
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::my-private-vesper-backups/vesper/*"
    }
  ]
}
```

Create an access key under IAM → Users → the upload-only user → Security
credentials → Create access key, then put the returned values in the ignored
`backend/backup.env` file:

```dotenv
BACKUP_S3_URI=s3://my-private-vesper-backups/vesper
AWS_REGION=eu-west-3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Never commit `backend/backup.env` or put these credentials in the public
frontend. AWS documents S3 bucket creation, object uploads, and IAM policies in
[the S3 bucket guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/create-bucket-overview.html),
[the upload guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/upload-objects.html),
and [the S3 policy examples](https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-policies-s3.html).

To migrate existing Supabase data into the new local database, create an
external dump first, then restore it into the stopped local database:

```bash
BACKUP_DATABASE_SOURCE=url \
DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE' \
./deploy/backup.sh

set -a; . backend/.env; set +a
docker compose -f backend/docker-compose.yml up -d db
cat backups/trading-postgres-REPLACE_WITH_DUMP.dump | \
  docker compose -f backend/docker-compose.yml exec -T db \
  pg_restore --clean --if-exists --no-owner \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"
```

Confirm the dump filename and target database before restoring. Start the
application only after the restore completes.

To reclaim existing high-volume rows immediately after deployment, call the
admin-only retention endpoint:

```bash
curl -X POST -H "X-Vesper-Key: $VESPER_ADMIN_KEY" \
  "https://${VESPER_DOMAIN}/operator/retention/cleanup"
```

Do not configure `POLYMARKET_PRIVATE_KEY` for paper mode.

## 4. Start the backend and database

```bash
docker compose -f backend/docker-compose.yml up -d --build
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs --tail=200 db trading pipeline
```

Verify locally on EC2:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/ready
curl --fail -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/observability
curl --fail -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/alerts
```

The worker may initially be degraded while it collects fresh books. Inspect it with:

```bash
curl -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/pipeline/observations | jq
```

## 5. Enable HTTPS

```bash
docker compose -f backend/docker-compose.yml up -d nginx certbot
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs --tail=200 nginx certbot
```

Nginx starts in HTTP mode for ACME. Once Certbot creates the certificate, the Nginx entrypoint switches to HTTPS and reloads.

Verify:

```bash
curl -I "http://${VESPER_DOMAIN}/health"
curl -I "https://${VESPER_DOMAIN}/health"
```

HTTP should redirect to HTTPS after certificate issuance.

## 6. Deploy Cloudflare Pages frontend

In Cloudflare Pages:

1. Create a Pages project from the GitHub repository.
2. Set root directory to `frontend`.
3. Use Node.js 20 or newer.
4. Set build command to `npm run build`.
5. Set output directory to `dist`.

Public Pages environment variables:

```text
VITE_API_URL=https://YOUR_API_DOMAIN
VITE_API_KEY=client-key
```

Do **not** set `VITE_ADMIN_KEY` on a public frontend. Vite embeds environment variables into browser JavaScript. Admin controls must use an internal operator build or direct authenticated API calls.

After deployment, copy the actual Pages hostname into backend `.env`:

```dotenv
CORS_ORIGINS=https://your-project.pages.dev
```

Restart the backend:

```bash
docker compose -f backend/docker-compose.yml up -d --build trading pipeline
```

## 7. Verify CORS and browser access

```bash
curl -i -X OPTIONS "https://${VESPER_DOMAIN}/decide" \
  -H "Origin: https://your-project.pages.dev" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,x-vesper-key"
```

The response must contain the exact Pages origin. In the browser verify that decisions, orders, process metrics, scars, operations, data quality, and worker status load successfully.

## 8. Deployment validation

From the repository root on EC2:

```bash
chmod +x deploy/validate.sh deploy/backup.sh
export VESPER_API_URL="https://${VESPER_DOMAIN}"
export VESPER_API_KEY="your-client-key"
export VESPER_ADMIN_KEY="your-admin-key"
bash deploy/validate.sh
```

The validator checks Compose syntax, health, readiness, observability, and alerts.

## 9. Operations

```bash
docker compose -f backend/docker-compose.yml logs -f trading pipeline nginx
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/observability" | jq
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/alerts" | jq
curl -H "X-Vesper-Key: $VESPER_API_KEY" "$VESPER_API_URL/metrics/prometheus"
```

Critical alerts:

- `MARKET_DATA_STALE`
- `MARKET_DATA_QUALITY_LOW`
- `INGESTION_WORKER_STALE`
- `ERROR_BURST`

Keep the system in paper mode while any critical alert is active.

## 10. Backups and restore

The scheduled `backup` service runs every 15 minutes after PostgreSQL is
healthy. Inspect it with:

```bash
docker compose -f backend/docker-compose.yml logs --tail=100 backup
docker compose -f backend/docker-compose.yml exec backup ls -lh /backups
```

The container uploads each custom-format dump and SHA-256 checksum to
`BACKUP_S3_URI`. A private S3 bucket and upload-only IAM identity must be
configured in `backend/backup.env` before starting the full stack.

For an immediate manual backup or recovery operation:

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh
```

The script creates a restricted PostgreSQL custom-format dump from the Docker
database plus a SHA-256 checksum. Set an upload command before production use.
The file path is available to the command as `$1`:

```bash
export BACKUP_UPLOAD_COMMAND='rclone copy "$1" remote:vesper/backups'
./deploy/backup.sh
```

The upload command must include its destination configuration. Test restoration
on a separate database regularly; an untested archive is not a verified backup.

To restore a dump after stopping the writers, copy it into the database
container and use `pg_restore --clean --if-exists --no-owner`. Restore only
after confirming the target database and backup filename, because this replaces
existing database objects.

## 11. Upgrade and rollback

```bash
git fetch --all --prune
git checkout <approved-commit>
docker compose -f backend/docker-compose.yml up -d --build trading pipeline nginx
bash deploy/validate.sh
```

Rollback uses the same process with the previous approved commit. Back up before migrations or destructive recovery work.

## 12. Key rotation

```bash
curl -X POST \
  -H "X-Vesper-Key: $VESPER_ADMIN_KEY" \
  "${VESPER_API_URL}/operator/rotate-key?scope=trade"
```

Store the returned key in a secret manager, update EC2 and Pages configuration, redeploy, then revoke old credentials after migration. Never put admin credentials in a public browser bundle.

## 13. Troubleshooting

### Authentication errors

`401` means the key is missing or invalid. `403` means the key lacks the required scope. Client keys cannot perform admin operations.

### CORS errors

Set `CORS_ORIGINS` to the exact Pages origin without a trailing slash, then rebuild/restart the API.

### Stale worker

```bash
docker compose -f backend/docker-compose.yml logs --tail=300 pipeline
docker compose -f backend/docker-compose.yml restart pipeline
```

### Certificate failure

Check DNS, ports 80/443, and Certbot logs:

```bash
getent hosts "$VESPER_DOMAIN"
docker compose -f backend/docker-compose.yml logs certbot nginx
```

### Unhealthy service

```bash
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs --tail=300 trading
docker compose -f backend/docker-compose.yml restart trading pipeline
```

## 14. Production sign-off

- [ ] Elastic IP and correct DNS.
- [ ] SSH restricted to operator IP.
- [ ] Ports 80/443 only publicly exposed.
- [ ] Port 8000 private.
- [ ] HTTPS active.
- [ ] Exact Pages origin in `CORS_ORIGINS`.
- [ ] Authentication enabled.
- [ ] Client and admin keys differ.
- [ ] Admin key absent from public frontend.
- [ ] `LIVE_TRADING_ENABLED=false`.
- [ ] Live capital and order limits are zero.
- [ ] Book-required ingestion is enabled.
- [ ] `/ready` is healthy.
- [ ] No unresolved critical alerts.
- [ ] Backup completed and copied off-host.
- [ ] Restore process tested.
- [ ] `bash deploy/validate.sh` passes.

## 15. Live boundary

Keep the deployment in paper/shadow mode until authenticated CLOB submission, signer/funder verification, allowance checks, partial-fill reconciliation, cancellation/retry policy, balance reconciliation, outage handling, and an independent emergency kill switch are implemented and tested with a controlled account.
