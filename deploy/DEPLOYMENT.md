# Vesper Trading: EC2 + Cloudflare Pages deployment

This guide deploys the Vesper backend and ingestion worker on Ubuntu EC2, with Nginx/Certbot HTTPS, and the React frontend on Cloudflare Pages. The deployment is paper-first: live trading remains disabled.

## Architecture

```text
Cloudflare Pages -> HTTPS -> EC2 Nginx -> trading API :8000
                                      -> shared trading-data volume
                                      -> ingestion worker
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
chmod 600 backend/.env
openssl rand -base64 48
openssl rand -base64 48
openssl rand -base64 48
nano backend/.env
```

Use separate generated values for the client key, admin key, and operator approval code. Never commit `backend/.env`.

Recommended paper/shadow values:

```dotenv
CORS_ORIGINS=https://YOUR-PAGES-HOST.pages.dev
VESPER_DOMAIN=api.example.com
CERTBOT_EMAIL=ops@example.com

SIBYL_OFFICIAL=1
SIBYL_DB_PATH=/app/data/trading.db
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

VESPER_AUTH_REQUIRED=true
VESPER_API_KEY=long-random-client-key
VESPER_ADMIN_KEY=long-random-admin-key
VESPER_RATE_LIMIT_PER_MINUTE=120

LIVE_TRADING_ENABLED=false
MAX_LIVE_CAPITAL=0
MAX_LIVE_ORDER_SIZE=0
```

Do not configure `POLYMARKET_PRIVATE_KEY` for paper mode.

## 4. Start the backend

```bash
docker compose -f backend/docker-compose.yml up -d --build trading pipeline
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs --tail=200 trading pipeline
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

```bash
./deploy/backup.sh
scp backups/trading-memory-*.tgz backup-host:/secure/vesper/
```

The script stops both API writers before copying the persistent volume. Test restoration on a separate host regularly; an untested archive is not a verified backup.

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
