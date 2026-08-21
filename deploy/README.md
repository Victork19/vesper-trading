# Vesper Trading deployment

The entire EC2 backend stack is Dockerized: API, ingestion worker, Nginx reverse proxy, and Certbot. After DNS and `.env` are configured, one Compose command starts the system.

This guide deploys the backend and ingestion worker on AWS EC2 and the Vite frontend on Cloudflare Pages.

- API: `https://vesper-scar.duckdns.org`
- Backend: Docker Compose on Ubuntu EC2
- Frontend: Cloudflare Pages
- HTTPS: Nginx and Let's Encrypt
- Initial mode: paper

## 1. EC2 and security group

Use Ubuntu 22.04/24.04. Configure the EC2 security group as follows:

| Port | Source | Purpose |
|---|---|---|
| 22 | Your IP only | SSH |
| 80 | `0.0.0.0/0` | HTTP and Let's Encrypt |
| 443 | `0.0.0.0/0` | HTTPS API |

Do not expose port 8000 publicly. Nginx will proxy to it locally.

```bash
ssh -i /path/to/key.pem ubuntu@EC2_PUBLIC_IP
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker --version
docker compose version
```

## 2. DuckDNS

Create this record in DuckDNS:

```text
vesper-scar.duckdns.org -> EC2_PUBLIC_IP
```

Use the public address, not the private `172.31.x.x` address.

```bash
getent hosts vesper-scar.duckdns.org
```

## 3. Clone and configure

```bash
cd ~
git clone https://github.com/YOUR_USER/vesper-trading.git
cd ~/vesper-trading
cp backend/.env.example backend/.env
nano backend/.env
```

Recommended initial values:

```dotenv
CORS_ORIGINS=https://YOUR-PAGES-PROJECT.pages.dev
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-REGION.pooler.supabase.com:5432/postgres
DATABASE_POOL_MAX=4
SIBYL_OFFICIAL=0
TRADING_MODE=paper
POLYMARKET_GAMMA_URL=https://gamma-api.polymarket.com
POLYMARKET_CLOB_URL=https://clob.polymarket.com
PIPELINE_INTERVAL_SECONDS=60
INGEST_MARKET_LIMIT=50
MIN_MARKET_SNAPSHOTS=1000
MIN_DATA_QUALITY=0.95
LIVE_TRADING_ENABLED=false
VESPER_AUTH_REQUIRED=true
VESPER_API_KEY=use-a-long-random-client-key
VESPER_ADMIN_KEY=use-a-separate-long-random-admin-key
MAX_LIVE_CAPITAL=0
MAX_LIVE_ORDER_SIZE=0
```

Never commit `backend/.env`. Do not put a private key in it for paper mode.

## 4. Database

Postgres is the source of truth. Use the Supabase shared Supavisor session-mode connection string on port `5432`; the free-tier shared pooler is IPv4-compatible, so no EC2 IPv6 setup is required. Sibyl is optional and disabled by default.

## 5. Start the API and ingestion worker

```bash
docker compose -f backend/docker-compose.yml up -d --build
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs -f trading pipeline
```

Test directly on EC2:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/pipeline/status
curl -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/observability
curl -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/alerts
curl -H "X-Vesper-Key: $VESPER_API_KEY" http://127.0.0.1:8000/metrics/prometheus
```

## 6. Start the complete Docker stack

Set these values in `backend/.env`:

```dotenv
VESPER_DOMAIN=vesper-scar.duckdns.org
CERTBOT_EMAIL=YOUR_EMAIL
```

Then start everything:

```bash
docker compose -f backend/docker-compose.yml up -d --build
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs -f nginx certbot
```

Nginx initially serves HTTP so Certbot can complete the ACME challenge. When the certificate is created, the Nginx container automatically reloads into HTTPS mode. Renewals run inside the Certbot container.

Verify:

```bash
curl http://vesper-scar.duckdns.org/health
curl https://vesper-scar.duckdns.org/health
```

Port 8000 is internal to the Compose network and is not published to the host.

## 7. Optional manual certificate recovery

If the first automatic issuance fails, inspect the logs:

```bash
docker compose -f backend/docker-compose.yml logs certbot nginx
```

Confirm that DuckDNS points to the public EC2 IP and ports 80/443 are allowed. Then retry:

```bash
docker compose -f backend/docker-compose.yml restart certbot
```

## 8. Cloudflare Pages

Connect the GitHub repository in Cloudflare Pages and use:

```text
Root directory: frontend
Build command: npm run build
Build output directory: dist
Node version: 20 or newer
```

Add this Pages environment variable:

```text
VITE_API_URL=https://vesper-scar.duckdns.org
VITE_API_KEY=the-client-key-from-your-secret-store
# Only expose VITE_ADMIN_KEY in a protected internal operator build.
VITE_ADMIN_KEY=the-admin-key-from-your-secret-store
```

After the first deployment, copy the real Pages hostname and update EC2 `backend/.env`:

```dotenv
CORS_ORIGINS=https://YOUR-REAL-PAGES-HOST.pages.dev
```

Then restart:

```bash
docker compose -f backend/docker-compose.yml up -d --build trading pipeline
```

If you use multiple frontend origins, separate them with commas. Rebuild Cloudflare Pages after changing `VITE_API_URL`.

## 9. Verify CORS and the frontend

```bash
curl -i -X OPTIONS https://vesper-scar.duckdns.org/decide \
  -H "Origin: https://YOUR-REAL-PAGES-HOST.pages.dev" \
  -H "Access-Control-Request-Method: POST"
```

The response must include `access-control-allow-origin` with the exact Pages hostname.

Then verify:

```bash
curl https://vesper-scar.duckdns.org/health
curl https://vesper-scar.duckdns.org/ready
curl https://vesper-scar.duckdns.org/dashboard
curl https://vesper-scar.duckdns.org/pipeline/observations
```

Open the Pages URL and confirm browser requests use `https://vesper-scar.duckdns.org`.

## 10. Backups and operations

Operational telemetry is available at `/observability`, active alerts at `/alerts`, and Prometheus-compatible metrics at `/metrics/prometheus`. Scrapers must send `X-Vesper-Key`; keep this endpoint behind an internal network policy.

API credentials are scoped: the client key can read and submit paper/shadow decisions, while the admin key controls mode and operator actions. Rotate short-lived process-local credentials with `POST /operator/rotate-key` and then persist the returned value in the VPS secret manager/environment before restarting. Never ship `VITE_ADMIN_KEY` in a public frontend build; use it only for an internal operator build.

The database is hosted by Supabase Postgres. Use `deploy/backup.sh` for `pg_dump` backups.

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml logs --tail=200 trading
docker compose -f backend/docker-compose.yml logs --tail=200 pipeline
```

Copy backups off the EC2 instance and periodically test restoration. A backup on the same disk is not disaster recovery.

## 11. Live-mode safety

Keep these values unchanged during initial deployment:

```dotenv
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
MAX_LIVE_CAPITAL=0
MAX_LIVE_ORDER_SIZE=0
```

The ingestion pipeline may collect data automatically, but data readiness never enables live trading. Live mode additionally requires authenticated exchange integration, verified limits, reconciliation, emergency shutdown testing, and explicit operator approval. The current live adapter remains fail-closed until those controls are completed.

## Troubleshooting

For `Failed to fetch`, verify all of the following:

1. EC2 allows ports 80 and 443.
2. DuckDNS points to the public EC2 IP.
3. The Docker Nginx service is running: `docker compose -f backend/docker-compose.yml ps nginx`.
4. HTTPS works: `curl https://vesper-scar.duckdns.org/health`.
5. `VITE_API_URL` uses `https://`.
6. `CORS_ORIGINS` exactly matches the Cloudflare Pages hostname.
7. Cloudflare Pages was rebuilt after changing its environment variable.

For unhealthy containers:

```bash
docker compose -f backend/docker-compose.yml logs trading
docker compose -f backend/docker-compose.yml logs pipeline
docker compose -f backend/docker-compose.yml restart
```

This deployment is appropriate for paper and shadow operation. Institutional live operation additionally requires production databases, durable event streaming, authenticated order lifecycle management, independent risk infrastructure, monitoring, secrets management, and disaster recovery.
